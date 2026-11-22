"""Markdown-to-HTML pipeline with masking, sanitization, alerts, TOC, H1 dedup, and CSP."""

from __future__ import annotations

import html
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlparse

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml, unescapeAll
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from mdtopdf_skill.models import DEFAULT_LANG, ConversionOptions, FrontmatterData
from mdtopdf_skill.sanitizer import (
    ASSETS_DIR,
    resolve_local_image,
    sanitize_css,
    sanitize_html,
)

__all__ = ["BuiltDocument", "build_html_document"]

_THAI_CHAR_RE = re.compile(r"[\u0e00-\u0e7f]")
_FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})([^\n]*)$")
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_HTML_TAG_RE = re.compile(
    r"<[a-zA-Z][a-zA-Z0-9:-]*"
    r"(?:\s+[a-zA-Z_:][a-zA-Z0-9_.:-]*(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))?)*"
    r"\s*/?>"
)
_TABLE_SEP_ROW_RE = re.compile(
    r"^[ \t]*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?[ \t]*$"
    r"|^[ \t]*\|?\s*:?-{3,}:?\s*\|[ \t]*$",
    flags=re.MULTILINE,
)
_CURRENCY_START_RE = re.compile(
    r"^\d[\d,]*(?:\.\d+)?(?=$|[\s,;!?)\]}]|(?:\.(?=$|[\s,;!?)\]}]))|-(?=\s|\$))"
)
_DIGIT_MATH_OP_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?\s*(?:[+*/=<>^_\\]|-(?!\s*\$))")
_SPACED_CURRENCY_INNER_RE = re.compile(r"^\d[\d,.\s]*\s[\d,.\s]*$")

_BQ_CONSECUTIVE_RE = re.compile(
    r"^([ \t]*>[^\n]*\n)(?:[ \t]*\n)+(?=[ \t]*>)",
    flags=re.MULTILINE,
)
_LEADING_ATX_H1_RE = re.compile(
    r"^(?:[ \t]*\n)*([ \t]{0,3}#[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?(?:\n|$)(?:[ \t]*\n)*)"
)

_IMG_TAG_RE = re.compile(r"<img\b([^>]*)>", flags=re.IGNORECASE)
_IMG_SRC_ATTR_RE = re.compile(
    r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
    flags=re.IGNORECASE,
)

_ALERT_OPEN_RE = re.compile(
    r"^\s*<p>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\](?:[ \t]*\n|[ \t]+|(?=</p>))",
    flags=re.IGNORECASE,
)
_TASK_ITEM_RE = re.compile(
    r"<li(?P<attrs>\s+[^>]*)?>(?P<pre>\s*(?:<p\b[^>]*>)?\s*)"
    r"\[(?P<mark>[ xX])\](?:\s+|(?=</(?:p|li)>))",
    flags=re.IGNORECASE,
)

_WIN_SLASH_DRIVE_RE = re.compile(r"^/([A-Za-z])[:|]/(.*)$")
_WIN_DRIVE_REL_RE = re.compile(r"^[A-Za-z]:(?![\\/])")
_WIN_DRIVE_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_LOCAL_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]{1,}):")

_LIST_ITEM_RE = re.compile(r"^([ ]*)((?:[-*+])|(?:\d{1,9}[.)]))([ \t]+)(.*)$")
_THEMATIC_BREAK_RE = re.compile(
    r"^[ ]*(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$"
)
_ANY_FENCE_RE = re.compile(r"^([ ]*)(`{3,}|~{3,})([^\n]*)$")
_LIST_MARKER_PREFIX_RE = re.compile(r"^([ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+)")
_BLOCK_INTERRUPT_RE = re.compile(r"^[ ]{0,3}(?:#{1,6}(?:[ \t]|$)|>)")

_FOOTNOTE_DEF_RE = re.compile(r"^[ ]{0,3}\[\^([^\s\]]+)\]:[ \t]*(.*)$")
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\s\]]+)\](?!\()")

_HEADING_TAG_RE = re.compile(r"<h([1-6])([^>]*)>([\s\S]*?)</h\1>", flags=re.IGNORECASE)
_ID_ATTR_RE = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", flags=re.IGNORECASE)
_TOC_PARAGRAPH_RE = re.compile(r"<p>\s*\[TOC\]\s*</p>")

_HIGHLIGHT_FORMATTER = HtmlFormatter(cssclass="highlight")


@dataclass(frozen=True)
class BuiltDocument:
    """Compiled, sanitized, self-contained HTML document ready for CDP rendering."""

    html: str
    suppressed_images: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    has_mermaid: bool = False
    has_math: bool = False
    nonce: str = ""


def _make_token_prefix(base: str, text: str) -> str:
    """Return an ASCII-only token prefix guaranteed not to appear in ``text``."""
    prefix = base
    while prefix in text:
        prefix = f"{base}{secrets.token_hex(4).upper()}"
    return prefix


def _is_escaped(text: str, pos: int) -> bool:
    """Return True if character at ``pos`` is preceded by an odd number of backslashes."""
    backslashes = 0
    idx = pos - 1
    while idx >= 0 and text[idx] == "\\":
        backslashes += 1
        idx -= 1
    return (backslashes % 2) == 1


def _mask_fenced_and_mermaid(
    text: str,
    code_prefix: str,
    mermaid_prefix: str,
    code_masks: dict[str, str],
    mermaid_blocks: list[str],
) -> str:
    """Extract ```mermaid/~~~mermaid blocks and mask non-Mermaid fenced code blocks."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        match = _FENCE_OPEN_RE.match(lines[i])
        if match is None:
            out.append(lines[i])
            i += 1
            continue

        fence = match.group(1)
        info = match.group(2)
        fence_char = fence[0]
        fence_len = len(fence)

        if fence_char == "`" and "`" in info:
            out.append(lines[i])
            i += 1
            continue

        close_re = re.compile(rf"^[ ]{{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*$")
        close_idx = -1
        for j in range(i + 1, n):
            if close_re.match(lines[j]):
                close_idx = j
                break

        if close_idx == -1:
            out.append(lines[i])
            i += 1
            continue

        info_words = info.strip().split()
        if info_words and info_words[0].lower() == "mermaid":
            mermaid_src = "\n".join(lines[i + 1 : close_idx])
            m_idx = len(mermaid_blocks)
            mermaid_blocks.append(mermaid_src)
            out.append("")
            out.append(f"{mermaid_prefix}{m_idx}END")
            out.append("")
        else:
            token = f"{code_prefix}{len(code_masks)}END"
            code_masks[token] = "\n".join(lines[i : close_idx + 1])
            out.append(token)
        i = close_idx + 1

    return "\n".join(out)


def _mask_inline_code(
    text: str,
    code_prefix: str,
    code_masks: dict[str, str],
) -> str:
    """Mask CommonMark inline code spans outside fenced blocks."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "`" and not _is_escaped(text, i):
            tick_len = 1
            while i + tick_len < n and text[i + tick_len] == "`":
                tick_len += 1

            j = i + tick_len
            close_pos = -1
            while j < n:
                if text[j] == "\n":
                    k = j + 1
                    while k < n and text[k] in (" ", "\t"):
                        k += 1
                    if k < n and text[k] == "\n":
                        break
                    j += 1
                    continue
                if text[j] == "`":
                    run = 1
                    while j + run < n and text[j + run] == "`":
                        run += 1
                    if run == tick_len:
                        close_pos = j
                        break
                    j += run
                    continue
                j += 1

            if close_pos != -1:
                end_pos = close_pos + tick_len
                token = f"{code_prefix}{len(code_masks)}END"
                code_masks[token] = text[i:end_pos]
                out.append(token)
                i = end_pos
                continue

            out.append(text[i : i + tick_len])
            i += tick_len
            continue

        out.append(text[i])
        i += 1

    return "".join(out)


def _find_matching_bracket(text: str, open_bracket_idx: int) -> int:
    """Find matching ']' for '[' at ``open_bracket_idx``, or -1."""
    depth = 1
    i = open_bracket_idx + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i
        elif ch == "\n":
            k = i + 1
            while k < n and text[k] in (" ", "\t"):
                k += 1
            if k < n and text[k] == "\n":
                return -1
        i += 1
    return -1


def _parse_markdown_link_dest(
    text: str, open_paren_idx: int, *, allow_spaces: bool = False
) -> tuple[str, str, int] | None:
    """Parse `(dest [title])` starting at '('; return (raw_src, optional_title_tail, end_idx)."""
    n = len(text)
    i = open_paren_idx + 1
    while i < n and text[i] in (" ", "\t"):
        i += 1
    if i < n and text[i] == "\n":
        i += 1
        while i < n and text[i] in (" ", "\t"):
            i += 1
    if i >= n:
        return None

    starts_with_angle = text[i] == "<"
    if starts_with_angle:
        i += 1
        dest_start = i
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch in ("\n", "<"):
                return None
            if ch == ">":
                raw_src = text[dest_start:i]
                i += 1
                break
            i += 1
        else:
            return None
    else:
        dest_start = i
        paren_depth = 0
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch in (" ", "\t", "\n"):
                break
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                if paren_depth == 0:
                    break
                paren_depth -= 1
            i += 1
        raw_src = text[dest_start:i]

    tail_start = i
    while i < n and text[i] in (" ", "\t", "\n"):
        i += 1
    if i >= n:
        return None
    if text[i] == ")":
        return (raw_src, text[tail_start:i], i + 1)

    if text[i] in ('"', "'", "("):
        close_ch = ")" if text[i] == "(" else text[i]
        i += 1
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == close_ch:
                i += 1
                break
            i += 1
        else:
            return None
        while i < n and text[i] in (" ", "\t"):
            i += 1
        if i < n and text[i] == ")":
            return (raw_src, text[tail_start:i], i + 1)

    if allow_spaces and not starts_with_angle:
        k = dest_start
        paren_depth = 0
        while k < n:
            ch = text[k]
            if ch == "\\" and k + 1 < n:
                k += 2
                continue
            if ch in ("\n", "<", ">", '"', "'"):
                return None
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                if paren_depth == 0:
                    spaced_src = text[dest_start:k].rstrip(" \t")
                    return (spaced_src, "", k + 1) if spaced_src else None
                paren_depth -= 1
            k += 1

    return None


def _resolve_markdown_images(
    text: str,
    base_dir: str,
    options: ConversionOptions,
    suppressed_images: list[str],
) -> str:
    """Resolve `![alt](dest)` images pre-Markdown so UNC backslashes are preserved."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "!" and i + 1 < n and text[i + 1] == "[" and not _is_escaped(text, i):
            close_br = _find_matching_bracket(text, i + 1)
            if close_br != -1 and close_br + 1 < n and text[close_br + 1] == "(":
                parsed_dest = _parse_markdown_link_dest(
                    text, close_br + 1, allow_spaces=True
                )
                if parsed_dest is not None:
                    raw_src, tail, end_idx = parsed_dest
                    alt_text = text[i + 2 : close_br]
                    allowed, exists, resolved_path = resolve_local_image(
                        raw_src,
                        base_dir,
                        allow_outside_base_dir=options.allow_outside_base_dir,
                    )
                    if not allowed:
                        suppressed_images.append(raw_src)
                        i = end_idx
                        continue
                    if not exists and options.suppress_missing_images:
                        suppressed_images.append(raw_src)
                        i = end_idx
                        continue
                    if resolved_path is not None:
                        out.append(f"![{alt_text}](<{resolved_path.as_uri()}>{tail})")
                    else:
                        out.append(text[i:end_idx])
                    i = end_idx
                    continue

        out.append(text[i])
        i += 1

    return "".join(out)


def _mask_links_and_images_for_math(
    text: str,
    temp_prefix: str,
    temp_masks: dict[str, str],
) -> str:
    """Temporarily mask `![alt](dest)` and `](dest)` so `$` in alt/URLs is ignored."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "!" and i + 1 < n and text[i + 1] == "[" and not _is_escaped(text, i):
            close_br = _find_matching_bracket(text, i + 1)
            if close_br != -1 and close_br + 1 < n and text[close_br + 1] == "(":
                parsed = _parse_markdown_link_dest(text, close_br + 1)
                if parsed is not None:
                    _, _, end_idx = parsed
                    token = f"{temp_prefix}{len(temp_masks)}END"
                    temp_masks[token] = text[i:end_idx]
                    out.append(token)
                    i = end_idx
                    continue
        if text[i] == "]" and i + 1 < n and text[i + 1] == "(":
            parsed = _parse_markdown_link_dest(text, i + 1)
            if parsed is not None:
                _, _, end_idx = parsed
                token = f"{temp_prefix}{len(temp_masks)}END"
                temp_masks[token] = text[i + 1 : end_idx]
                out.append("]")
                out.append(token)
                i = end_idx
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _normalize_ordered_list_indents(text: str) -> str:
    """Align 2/3-space nested sub-items and continuations to parent list content columns."""
    lines = text.split("\n")
    out: list[str] = []
    # Stack entries: (orig_indent, out_indent, out_content_col, shift)
    stack: list[tuple[int, int, int, int]] = []
    in_fence: tuple[str, int, int] | None = None
    prev_blank = False

    for line in lines:
        if in_fence is not None:
            f_char, f_len, f_shift = in_fence
            out.append((" " * f_shift) + line if f_shift > 0 and line.strip() else line)
            if re.match(rf"^[ ]*{re.escape(f_char)}{{{f_len},}}[ \t]*$", line):
                in_fence = None
            prev_blank = False
            continue

        if not line.strip():
            out.append(line)
            prev_blank = True
            continue

        # Normalize leading tabs on non-fence lines to CommonMark 4-space tab stops.
        ws_len = len(line) - len(line.lstrip(" \t"))
        lead_spaces = line[:ws_len].expandtabs(4)
        norm_line = lead_spaces + line[ws_len:]
        orig_indent = len(lead_spaces)
        stripped_lead = norm_line[orig_indent:]

        fence_m = _ANY_FENCE_RE.match(norm_line)
        if fence_m is not None and not (
            fence_m.group(2)[0] == "`" and "`" in fence_m.group(3)
        ):
            while stack and orig_indent <= stack[-1][0]:
                stack.pop()
            applied_shift = 0
            if stack:
                _, _, p_content_col, p_shift = stack[-1]
                cand = orig_indent + p_shift
                applied_shift = (
                    p_content_col - orig_indent if cand < p_content_col else p_shift
                )
            out.append((" " * applied_shift) + norm_line)
            in_fence = (fence_m.group(2)[0], len(fence_m.group(2)), applied_shift)
            prev_blank = False
            continue

        is_thematic = _THEMATIC_BREAK_RE.match(norm_line) is not None
        list_m = None if is_thematic else _LIST_ITEM_RE.match(norm_line)

        if list_m is not None:
            marker = list_m.group(2)
            sep = list_m.group(3)
            rest = list_m.group(4)
            while stack and orig_indent <= stack[-1][0]:
                stack.pop()
            if stack:
                _, _, p_content_col, p_shift = stack[-1]
                out_indent = max(orig_indent + p_shift, p_content_col)
            else:
                out_indent = orig_indent
            shift = out_indent - orig_indent
            sep_width = len(sep.expandtabs(4))
            out_content_col = out_indent + len(marker) + sep_width
            stack.append((orig_indent, out_indent, out_content_col, shift))
            out.append((" " * out_indent) + marker + sep + rest)
            prev_blank = False
            continue

        if prev_blank or is_thematic or _BLOCK_INTERRUPT_RE.match(norm_line) is not None:
            while stack and orig_indent <= stack[-1][0]:
                stack.pop()

        if stack and orig_indent > stack[-1][0]:
            _, _, p_content_col, p_shift = stack[-1]
            out_indent = max(orig_indent + p_shift, p_content_col)
            out.append((" " * out_indent) + stripped_lead)
        else:
            out.append(line)
        prev_blank = False

    return "\n".join(out)


def _format_display_math_replacement(
    masked: str,
    open_pos: int,
    close_pos: int,
    token: str,
) -> str:
    """Format a masked display math token while preserving list/blockquote indentation."""
    line_start = masked.rfind("\n", 0, open_pos) + 1
    line_prefix = masked[line_start:open_pos]

    after_close = close_pos + 2
    next_nl = masked.find("\n", after_close)
    line_tail = masked[after_close:] if next_nl == -1 else masked[after_close:next_nl]
    has_trailing_content = bool(line_tail.strip(" \t"))

    if re.match(r"^[ \t]*>", line_prefix):
        return token

    if line_prefix and not line_prefix.strip(" \t"):
        suffix = f"\n\n{line_prefix}" if has_trailing_content else "\n\n"
        return f"\n\n{line_prefix}{token}{suffix}"

    marker_match = _LIST_MARKER_PREFIX_RE.match(line_prefix)
    if marker_match is not None:
        indent = " " * len(marker_match.group(1).expandtabs(4))
        suffix = f"\n\n{indent}" if has_trailing_content else "\n\n"
        if line_prefix == marker_match.group(1):
            return f"{token}{suffix}"
        return f"\n\n{indent}{token}{suffix}"

    lead_ws_len = len(line_prefix) - len(line_prefix.lstrip(" \t"))
    if lead_ws_len > 0:
        indent = " " * len(line_prefix[:lead_ws_len].expandtabs(4))
        suffix = f"\n\n{indent}" if has_trailing_content else "\n\n"
        return f"\n\n{indent}{token}{suffix}"

    return f"\n\n{token}\n\n"


def _mask_math_spans(
    text: str,
    temp_prefix: str,
    math_prefix: str,
    math_spans: list[tuple[str, bool]],
) -> str:
    """Mask $$...$$ and $...$ math spans while ignoring currency, URLs, HTML attrs, and tables."""
    temp_masks: dict[str, str] = {}

    def _stash_temp(match: re.Match[str]) -> str:
        token = f"{temp_prefix}{len(temp_masks)}END"
        temp_masks[token] = match.group(0)
        return token

    masked = _HTML_COMMENT_RE.sub(_stash_temp, text)
    masked = _HTML_TAG_RE.sub(_stash_temp, masked)
    masked = _mask_links_and_images_for_math(masked, temp_prefix, temp_masks)
    masked = _TABLE_SEP_ROW_RE.sub(_stash_temp, masked)

    # Pass 1: Display math $$...$$
    out_display: list[str] = []
    i = 0
    n = len(masked)
    while i < n:
        if masked[i : i + 2] == "$$" and not _is_escaped(masked, i):
            j = i + 2
            close_pos = -1
            while j < n - 1:
                if masked[j : j + 2] == "$$" and not _is_escaped(masked, j):
                    close_pos = j
                    break
                j += 1
            if close_pos != -1:
                inner = masked[i + 2 : close_pos]
                if inner.strip():
                    idx = len(math_spans)
                    math_spans.append((inner.strip(), True))
                    token = f"{math_prefix}{idx}END"
                    out_display.append(
                        _format_display_math_replacement(masked, i, close_pos, token)
                    )
                    i = close_pos + 2
                    continue
        out_display.append(masked[i])
        i += 1
    after_display = "".join(out_display)

    # Pass 2: Inline math $...$
    out_inline: list[str] = []
    i = 0
    n = len(after_display)
    while i < n:
        if (
            after_display[i] == "$"
            and not _is_escaped(after_display, i)
            and after_display[i : i + 2] != "$$"
            and i + 1 < n
            and not after_display[i + 1].isspace()
        ):
            after = after_display[i + 1 :]
            is_currency_start = (
                _CURRENCY_START_RE.match(after) is not None
                and _DIGIT_MATH_OP_RE.match(after) is None
            )
            if not is_currency_start:
                j = i + 1
                close_pos = -1
                while j < n:
                    if after_display[j] == "\n":
                        k = j + 1
                        while k < n and after_display[k] in (" ", "\t"):
                            k += 1
                        if k < n and after_display[k] == "\n":
                            break
                        j += 1
                        continue
                    if after_display[j : j + 2] == "$$" and not _is_escaped(after_display, j):
                        break
                    if (
                        after_display[j] == "$"
                        and not _is_escaped(after_display, j)
                        and not after_display[j - 1].isspace()
                        and (j + 1 >= n or not ("0" <= after_display[j + 1] <= "9"))
                    ):
                        close_pos = j
                        break
                    j += 1

                if close_pos != -1:
                    inner = after_display[i + 1 : close_pos]
                    if (
                        inner.strip()
                        and _SPACED_CURRENCY_INNER_RE.fullmatch(inner.strip()) is None
                    ):
                        idx = len(math_spans)
                        math_spans.append((inner, False))
                        out_inline.append(f"{math_prefix}{idx}END")
                        i = close_pos + 1
                        continue

        out_inline.append(after_display[i])
        i += 1
    after_inline = "".join(out_inline)

    for token, original in reversed(list(temp_masks.items())):
        after_inline = after_inline.replace(token, original)
    return after_inline


def _normalize_title_for_dedup(raw_title: str) -> str:
    """Normalize heading/title text by stripping inline Markdown, HTML, and extra whitespace."""
    cleaned = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", raw_title)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    cleaned = re.sub(r"(\*\*|__|~~|`)(.+?)\1", r"\2", cleaned)
    cleaned = re.sub(r"(?<!\w)(\*|_)(.+?)\1(?!\w)", r"\2", cleaned)
    cleaned = cleaned.replace("`", "")
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _deduplicate_leading_h1(md_text: str, dedup_title: str | None) -> str:
    """Strip leading ATX `# H1` when its normalized text matches ``dedup_title``."""
    if not dedup_title:
        return md_text
    norm_target = _normalize_title_for_dedup(dedup_title)
    if not norm_target:
        return md_text

    match = _LEADING_ATX_H1_RE.match(md_text)
    if match is None:
        return md_text

    norm_h1 = _normalize_title_for_dedup(match.group(2))
    if norm_h1 == norm_target:
        return md_text[match.end() :]
    return md_text


def _resolve_html_images(
    html_body: str,
    base_dir: str,
    options: ConversionOptions,
    suppressed_images: list[str],
) -> str:
    """Validate and rewrite or strip raw HTML `<img>` tags after sanitization."""

    def _replace_img(match: re.Match[str]) -> str:
        attrs_text = match.group(1)
        src_match = _IMG_SRC_ATTR_RE.search(attrs_text)
        if src_match is None:
            suppressed_images.append("[unsafe-src]")
            return ""
        raw_attr_val = src_match.group(1) or src_match.group(2) or src_match.group(3) or ""
        raw_src = html.unescape(raw_attr_val)
        allowed, exists, resolved_path = resolve_local_image(
            raw_src,
            base_dir,
            allow_outside_base_dir=options.allow_outside_base_dir,
        )
        if not allowed:
            suppressed_images.append(raw_src)
            return ""
        if not exists and options.suppress_missing_images:
            suppressed_images.append(raw_src)
            return ""
        if resolved_path is not None:
            safe_uri = html.escape(resolved_path.as_uri(), quote=True)
            new_attrs = (
                attrs_text[: src_match.start()]
                + f'src="{safe_uri}"'
                + attrs_text[src_match.end() :]
            )
            return f"<img{new_attrs}>"
        return match.group(0)

    return _IMG_TAG_RE.sub(_replace_img, html_body)


def _find_matching_close_tag(
    html_text: str,
    open_end: int,
    tag_name: str,
) -> tuple[int, int] | None:
    """Find balanced closing </tag_name> starting at ``open_end``."""
    tag_re = re.compile(rf"<(/?){tag_name}\b[^>]*>", flags=re.IGNORECASE)
    depth = 1
    for match in tag_re.finditer(html_text, open_end):
        if match.group(1) == "/":
            depth -= 1
            if depth == 0:
                return (match.start(), match.end())
        else:
            depth += 1
    return None


def _transform_github_alerts(html_body: str) -> str:
    """Transform `<blockquote><p>[!KIND]...` into `.callout` divs."""
    bq_open_re = re.compile(r"<blockquote\b[^>]*>", flags=re.IGNORECASE)
    out: list[str] = []
    pos = 0

    while True:
        open_match = bq_open_re.search(html_body, pos)
        if open_match is None:
            out.append(html_body[pos:])
            break

        out.append(html_body[pos : open_match.start()])
        close_span = _find_matching_close_tag(html_body, open_match.end(), "blockquote")
        if close_span is None:
            out.append(html_body[open_match.start() :])
            break

        close_start, close_end = close_span
        inner = html_body[open_match.end() : close_start]
        alert_match = _ALERT_OPEN_RE.match(inner)
        if alert_match is None:
            transformed_inner = _transform_github_alerts(inner)
            out.append(html_body[open_match.start() : open_match.end()])
            out.append(transformed_inner)
            out.append(html_body[close_start:close_end])
            pos = close_end
            continue

        kind = alert_match.group(1).upper()
        kind_lower = kind.lower()
        kind_title = kind.title()

        rest_after_marker = inner[alert_match.end() :]
        if rest_after_marker.lstrip().startswith("</p>"):
            end_p_idx = rest_after_marker.find("</p>")
            body_html = rest_after_marker[end_p_idx + 4 :].lstrip()
        else:
            body_html = "<p>" + rest_after_marker.lstrip()

        body_html = _transform_github_alerts(body_html)
        out.append(
            f'<div class="callout callout-{kind_lower}">'
            f'<p class="callout-title">{kind_title}</p>{body_html}</div>'
        )
        pos = close_end

    return "".join(out)


def _transform_task_lists(html_body: str) -> str:
    """Convert `[ ] ` and `[x] ` list items into disabled task checkboxes."""

    def _replace_task(match: re.Match[str]) -> str:
        attrs = match.group("attrs") or ""
        pre = match.group("pre")
        checked = match.group("mark").lower() == "x"
        if 'class="' in attrs.lower():
            attrs_out = re.sub(
                r'class="([^"]*)"',
                r'class="\1 task-item"',
                attrs,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            attrs_out = f' class="task-item"{attrs}'
        checkbox = '<input type="checkbox" disabled checked>' if checked else (
            '<input type="checkbox" disabled>'
        )
        return f"<li{attrs_out}>{pre}{checkbox} "

    return _TASK_ITEM_RE.sub(_replace_task, html_body)


def _replace_p_wrapped(html_text: str, token: str, replacement: str) -> str:
    """Replace `<p>token</p>` (or bare `token`) with ``replacement`` without regex backrefs."""
    pattern = re.compile(rf"<p>\s*{re.escape(token)}\s*</p>")

    def _repl(_match: re.Match[str]) -> str:
        return replacement

    return pattern.sub(_repl, html_text).replace(token, replacement)


def _restore_placeholders(
    html_body: str,
    mermaid_prefix: str,
    mermaid_blocks: list[str],
    math_prefix: str,
    math_spans: list[tuple[str, bool]],
) -> str:
    """Restore HTML-escaped Mermaid and KaTeX placeholders after HTML sanitization."""
    result = html_body
    for idx, src in enumerate(mermaid_blocks):
        token = f"{mermaid_prefix}{idx}END"
        escaped_src = html.escape(src, quote=True)
        div = (
            f'<div class="mermaid-block" data-mermaid-index="{idx}" '
            f'data-mermaid-src="{escaped_src}"></div>'
        )
        result = _replace_p_wrapped(result, token, div)

    for idx, (tex, is_display) in enumerate(math_spans):
        token = f"{math_prefix}{idx}END"
        escaped_tex = html.escape(tex, quote=False)
        if is_display:
            div = f'<div class="math-display" data-math-index="{idx}">{escaped_tex}</div>'
            result = _replace_p_wrapped(result, token, div)
        else:
            span = f'<span class="math-inline" data-math-index="{idx}">{escaped_tex}</span>'
            result = result.replace(token, span)

    return result


def _resolve_frontmatter_css_file(
    css_ref: str,
    base_dir: str,
    allow_outside_base_dir: bool,
) -> tuple[bool, Path | None]:
    """Check containment for a frontmatter `.css` path; return (allowed, resolved_path)."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in css_ref):
        return (False, None)
    if css_ref.startswith("//") or css_ref.startswith("\\\\"):
        return (False, None)
    if PureWindowsPath(css_ref).drive.startswith("\\\\"):
        return (False, None)

    scheme_match = _LOCAL_SCHEME_RE.match(css_ref)
    if scheme_match is not None:
        if scheme_match.group(1).lower() != "file":
            return (False, None)
        parsed = urlparse(css_ref)
        if parsed.netloc.lower() not in {"", "localhost"}:
            return (False, None)
        decoded = unquote(parsed.path)
        if (
            any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded)
            or decoded.startswith("//")
            or decoded.startswith("\\\\")
            or decoded.startswith("/\\\\")
            or PureWindowsPath(decoded).drive.startswith("\\\\")
        ):
            return (False, None)
        win_drive = _WIN_SLASH_DRIVE_RE.match(decoded)
        if win_drive is not None:
            decoded = f"{win_drive.group(1)}:/{win_drive.group(2)}"
        candidate_str = decoded
    else:
        decoded = unquote(css_ref)
        if (
            any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded)
            or decoded.startswith("//")
            or decoded.startswith("\\\\")
            or PureWindowsPath(decoded).drive.startswith("\\\\")
        ):
            return (False, None)
        candidate_str = decoded

    if _WIN_DRIVE_REL_RE.match(candidate_str):
        return (False, None)
    if not Path(candidate_str).is_absolute() and not _WIN_DRIVE_ABS_RE.match(candidate_str):
        candidate_str = candidate_str.replace("\\", "/")

    try:
        base_path = Path(base_dir).resolve(strict=False)
        raw_path = Path(candidate_str)
        candidate = (raw_path if raw_path.is_absolute() else (base_path / raw_path)).resolve(
            strict=False
        )
        if not allow_outside_base_dir and not candidate.is_relative_to(base_path):
            return (False, None)
    except (OSError, ValueError):
        return (False, None)

    return (True, candidate)


def _resolve_frontmatter_css(
    options: ConversionOptions,
    fm: FrontmatterData,
    base_dir: str,
    warnings: list[str],
) -> str:
    """Resolve and sanitize frontmatter CSS (inline snippet or relative `.css` file)."""
    raw_fm_css = options.frontmatter_css if options.frontmatter_css else (fm.css or "")
    stripped = raw_fm_css.strip()
    if not stripped:
        return ""

    is_css_file = (
        "\n" not in stripped
        and "\r" not in stripped
        and "{" not in stripped
        and stripped.lower().endswith(".css")
    )
    if is_css_file:
        allowed, candidate = _resolve_frontmatter_css_file(
            stripped, base_dir, options.allow_outside_base_dir
        )
        if not allowed or candidate is None:
            warnings.append(f"frontmatter_css_blocked: {stripped}")
            return ""
        if not candidate.is_file():
            warnings.append(f"frontmatter_css_missing: {stripped}")
            return ""
        try:
            css_text = candidate.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            warnings.append(f"frontmatter_css_missing: {stripped}")
            return ""
        return sanitize_css(css_text, base_dir, options.allow_outside_base_dir)

    return sanitize_css(stripped, base_dir, options.allow_outside_base_dir)


def _highlight_code_block(code: str, lang: str, _attrs: str) -> str:
    """Highlight fenced code blocks with Pygments when an explicit language is given."""
    lang_name = lang.strip()
    if not lang_name:
        return ""
    try:
        lexer = get_lexer_by_name(lang_name, stripall=False)
    except ClassNotFound:
        return ""
    return str(pygments_highlight(code, lexer, _HIGHLIGHT_FORMATTER))


class _PdfMarkdownIt(MarkdownIt):
    """MarkdownIt subclass permitting resolved local `file:///` and `data:image/*` URIs."""

    def validateLink(self, url: str) -> bool:
        """Allow local file:/// and data:image/* URIs; sanitizer.py enforces containment."""
        lowered = url.strip().lower()
        if lowered.startswith(("file:///", "data:image/")):
            return True
        return bool(super().validateLink(url))


def _build_markdown_parser() -> MarkdownIt:
    """Construct CommonMark parser with GFM tables/strikethrough and Pygments fences."""
    md = _PdfMarkdownIt("commonmark", {"html": True, "highlight": _highlight_code_block}).enable(
        ["table", "strikethrough"]
    )

    def _render_fence(
        renderer: RendererHTML,
        tokens: Sequence[Token],
        idx: int,
        options: OptionsDict,
        env: EnvType,
    ) -> str:
        _ = env
        token = tokens[idx]
        info = unescapeAll(token.info).strip() if token.info else ""
        lang_name = info.split(None, 1)[0] if info else ""
        lang_attrs = info[len(lang_name) :].lstrip() if info and len(info) > len(lang_name) else ""

        highlighted = ""
        if options.highlight is not None:
            highlighted = options.highlight(token.content, lang_name, lang_attrs) or ""

        if highlighted.startswith(('<div class="highlight"', "<pre")):
            return highlighted if highlighted.endswith("\n") else highlighted + "\n"

        code_body = highlighted or escapeHtml(token.content)
        if info:
            tmp_token = Token(type="", tag="", nesting=0, attrs=token.attrs.copy())
            tmp_token.attrJoin("class", options.langPrefix + lang_name)
            attrs_html = renderer.renderAttrs(tmp_token)
        else:
            attrs_html = renderer.renderAttrs(token)
        return f"<pre><code{attrs_html}>{code_body}</code></pre>\n"

    md.add_render_rule("fence", _render_fence)
    return md


def _strip_one_indent_level(line: str) -> str:
    """Strip one Markdown continuation indent (1 tab or up to 4 spaces)."""
    if line.startswith("\t"):
        return line[1:]
    spaces = 0
    while spaces < 4 and spaces < len(line) and line[spaces] == " ":
        spaces += 1
    return line[spaces:]


def _extract_footnotes(
    text: str, fn_prefix: str
) -> tuple[str, dict[str, str], list[tuple[str, str, int, str]]]:
    """Extract `[^id]: ...` definitions and mask `[^id]` references in order of appearance."""
    lines = text.split("\n")
    kept_lines: list[str] = []
    defs: dict[str, str] = {}
    i = 0
    n = len(lines)

    while i < n:
        match = _FOOTNOTE_DEF_RE.match(lines[i])
        if match is None:
            kept_lines.append(lines[i])
            i += 1
            continue

        fn_id = match.group(1)
        body_lines = [match.group(2)]
        j = i + 1
        while j < n:
            nxt = lines[j]
            if not nxt.strip():
                k = j + 1
                while k < n and not lines[k].strip():
                    k += 1
                if k < n and (lines[k].startswith("    ") or lines[k].startswith("\t")):
                    while j < k:
                        body_lines.append("")
                        j += 1
                    continue
                break
            if nxt.startswith("    ") or nxt.startswith("\t"):
                body_lines.append(_strip_one_indent_level(nxt))
                j += 1
                continue
            break

        if fn_id not in defs:
            defs[fn_id] = "\n".join(body_lines).strip()
        i = j

    body_text = "\n".join(kept_lines)
    if not defs:
        return (body_text, {}, [])

    # Record order of first reference and replace references with tokens
    ref_tokens: dict[str, str] = {}
    used_order: list[tuple[str, str, int, str]] = []

    def _replace_ref(m: re.Match[str]) -> str:
        if _is_escaped(body_text, m.start()):
            return m.group(0)
        fn_id = m.group(1)
        if fn_id not in defs:
            return m.group(0)
        if fn_id not in ref_tokens:
            num = len(used_order) + 1
            safe_slug = re.sub(r"[^A-Za-z0-9_.:-]+", "-", fn_id).strip("-") or f"n{num}"
            token = f"{fn_prefix}{num - 1}END"
            ref_tokens[fn_id] = token
            used_order.append((fn_id, safe_slug, num, token))
        return ref_tokens[fn_id]

    masked_body = _FOOTNOTE_REF_RE.sub(_replace_ref, body_text)
    return (masked_body, defs, used_order)


def _render_footnotes(
    html_body: str,
    defs: dict[str, str],
    used_order: list[tuple[str, str, int, str]],
    code_masks: dict[str, str],
    md: MarkdownIt,
) -> str:
    """Substitute footnote tokens with superscript links and append `.footnote` list."""
    if not used_order:
        return html_body

    result = html_body
    fn_items: list[str] = []
    for fn_id, safe_slug, num, token in used_order:
        sup_html = (
            f'<sup id="fnref:{safe_slug}">'
            f'<a class="footnote-ref" href="#fn:{safe_slug}">{num}</a></sup>'
        )
        result = result.replace(token, sup_html)

        raw_fn_md = defs[fn_id]
        for code_tok, original in reversed(list(code_masks.items())):
            raw_fn_md = raw_fn_md.replace(code_tok, original)

        rendered_fn = md.render(raw_fn_md).strip()
        backref = (
            f'&#160;<a class="footnote-backref" href="#fnref:{safe_slug}" '
            f'title="Jump back to footnote {num} in the text">&#8617;</a>'
        )
        if rendered_fn.endswith("</p>"):
            rendered_fn = rendered_fn[:-4] + backref + "</p>"
        else:
            rendered_fn = rendered_fn + backref
        fn_items.append(f'<li id="fn:{safe_slug}">{rendered_fn}</li>')

    footnote_section = (
        '<div class="footnote">\n'
        "<hr />\n"
        "<ol>\n"
        + "\n".join(fn_items)
        + "\n</ol>\n"
        "</div>"
    )
    return f"{result}\n{footnote_section}"


def _slugify_heading(label: str, used_slugs: set[str]) -> str:
    """Create a unique URL-safe heading slug matching Unicode/ASCII headings."""
    cleaned = re.sub(r"[^\w\s-]", "", label.lower())
    base = re.sub(r"[-\s]+", "-", cleaned).strip("-") or "section"
    slug = base
    suffix = 1
    while slug in used_slugs:
        slug = f"{base}-{suffix}"
        suffix += 1
    used_slugs.add(slug)
    return slug


def _build_toc_html(headings: list[tuple[int, str, str]]) -> str:
    """Render nested `<div class="toc">` HTML from `(level, slug, escaped_label)` entries."""
    parts: list[str] = ['<div class="toc">', '<span class="toctitle">Table of Contents</span>']
    level_stack: list[int] = []

    for lvl, slug, escaped_label in headings:
        if not level_stack or lvl > level_stack[-1]:
            parts.append("<ul>")
            level_stack.append(lvl)
        else:
            while len(level_stack) > 1 and level_stack[-1] > lvl:
                parts.append("</li></ul>")
                level_stack.pop()
            parts.append("</li>")
        parts.append(f'<li><a href="#{html.escape(slug, quote=True)}">{escaped_label}</a>')

    while level_stack:
        parts.append("</li></ul>")
        level_stack.pop()
    parts.append("</div>")
    return "".join(parts)


def _apply_heading_ids_and_toc(html_body: str, *, enable_toc: bool) -> str:
    """Add deduplicated `id` slugs to headings and expand/prepend `.toc` when requested."""
    used_slugs: set[str] = set()
    headings: list[tuple[int, str, str]] = []

    def _process_heading(match: re.Match[str]) -> str:
        level = int(match.group(1))
        attrs = match.group(2)
        inner_html = match.group(3)
        plain = re.sub(r"<[^>]+>", "", inner_html)
        label = re.sub(r"\s+", " ", html.unescape(plain)).strip()

        id_match = _ID_ATTR_RE.search(attrs)
        if id_match is not None:
            existing_id = id_match.group(1) or id_match.group(2) or id_match.group(3) or ""
            slug = existing_id or _slugify_heading(label, used_slugs)
            used_slugs.add(slug)
            new_attrs = attrs
        else:
            slug = _slugify_heading(label, used_slugs)
            new_attrs = f'{attrs} id="{html.escape(slug, quote=True)}"'

        if label:
            headings.append((level, slug, html.escape(label, quote=False)))
        return f"<h{level}{new_attrs}>{inner_html}</h{level}>"

    updated_html = _HEADING_TAG_RE.sub(_process_heading, html_body)

    if not enable_toc or not headings:
        return updated_html

    toc_html = _build_toc_html(headings)
    if _TOC_PARAGRAPH_RE.search(updated_html):
        return _TOC_PARAGRAPH_RE.sub(lambda _m: toc_html, updated_html, count=1)
    return f"{toc_html}\n{updated_html}"


def _resolve_theme_parts_lazy(
    options: ConversionOptions,
    fm: FrontmatterData,
    printable_height_mm: float,
    *,
    theme_css: str | None,
    metadata_header_html: str | None,
    dedup_title: str | None,
) -> tuple[str, str, str | None]:
    """Lazily import mdtopdf_skill.themes only if injected theme outputs are omitted."""
    if theme_css is not None and metadata_header_html is not None:
        return (theme_css, metadata_header_html, dedup_title)

    from mdtopdf_skill.themes import build_metadata_header_html, build_theme_css

    resolved_header = metadata_header_html
    resolved_dedup = dedup_title
    if resolved_header is None:
        resolved_header, header_dedup = build_metadata_header_html(options, fm)
        if resolved_dedup is None:
            resolved_dedup = header_dedup

    resolved_css = (
        theme_css if theme_css is not None else build_theme_css(options, printable_height_mm)
    )
    return (resolved_css, resolved_header, resolved_dedup)


def build_html_document(
    md_body: str,
    base_dir: str,
    options: ConversionOptions,
    fm: FrontmatterData,
    printable_height_mm: float,
    *,
    theme_css: str | None = None,
    metadata_header_html: str | None = None,
    dedup_title: str | None = None,
) -> BuiltDocument:
    """Compile Markdown into a sanitized, CSP-protected HTML5 document."""
    # Stage 1: Normalize input & auto-detect Thai/English lang
    text = md_body.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if fm.lang is not None:
        effective_lang = fm.lang
    elif options.lang == DEFAULT_LANG:
        effective_lang = "th" if _THAI_CHAR_RE.search(text) else "en"
    else:
        effective_lang = options.lang

    suppressed_images: list[str] = []
    warnings: list[str] = []

    # Stage 2: Collision-safe ASCII token prefixes
    code_prefix = _make_token_prefix("MDTOPDFCODE", text)
    mermaid_prefix = _make_token_prefix("MDTOPDFMERMAID", text)
    math_prefix = _make_token_prefix("MDTOPDFMATH", text)
    temp_prefix = _make_token_prefix("MDTOPDFTMP", text)
    fn_prefix = _make_token_prefix("MDTOPDFFN", text)

    code_masks: dict[str, str] = {}
    mermaid_blocks: list[str] = []
    math_spans: list[tuple[str, bool]] = []

    # Stage 3 & 4: Mask fenced code/Mermaid blocks and inline code spans
    text = _mask_fenced_and_mermaid(text, code_prefix, mermaid_prefix, code_masks, mermaid_blocks)
    text = _mask_inline_code(text, code_prefix, code_masks)

    # Stage 4b: Normalize 2/3-space nested lists under ordered lists to parent content columns
    text = _normalize_ordered_list_indents(text)

    # Stage 5: Resolve Markdown images pre-Markdown (preserving UNC backslashes)
    text = _resolve_markdown_images(text, base_dir, options, suppressed_images)

    # Stage 6: Mask math spans & non-math contexts, then extract footnotes
    text = _mask_math_spans(text, temp_prefix, math_prefix, math_spans)
    text, fn_defs, fn_used = _extract_footnotes(text, fn_prefix)

    # Stage 7: Separate consecutive blockquotes, resolve theme outputs, dedup leading H1
    text = _BQ_CONSECUTIVE_RE.sub(r"\1\n<!-- mdtopdf-bq-sep -->\n\n", text)
    for token, original in reversed(list(code_masks.items())):
        text = text.replace(token, original)

    resolved_theme_css, resolved_header_html, resolved_dedup = _resolve_theme_parts_lazy(
        options,
        fm,
        printable_height_mm,
        theme_css=theme_css,
        metadata_header_html=metadata_header_html,
        dedup_title=dedup_title,
    )
    if resolved_header_html:
        text = _deduplicate_leading_h1(text, resolved_dedup)

    # Stage 8: Compile Markdown with CommonMark/GFM parser, footnotes, heading IDs, and TOC
    md = _build_markdown_parser()
    html_body = md.render(text)
    html_body = _render_footnotes(html_body, fn_defs, fn_used, code_masks, md)
    html_body = _apply_heading_ids_and_toc(html_body, enable_toc=bool(options.toc))

    # Stage 9: Sanitize HTML & resolve remaining HTML <img> tags
    html_body = sanitize_html(html_body)
    html_body = _resolve_html_images(html_body, base_dir, options, suppressed_images)

    # Stage 10: Post-sanitization transforms & Mermaid/Math restoration
    html_body = _transform_github_alerts(html_body)
    html_body = _transform_task_lists(html_body)
    html_body = _restore_placeholders(
        html_body, mermaid_prefix, mermaid_blocks, math_prefix, math_spans
    )

    # Stage 11: Sanitize frontmatter/CLI CSS & assemble final HTML document
    fm_css_sanitized = _resolve_frontmatter_css(options, fm, base_dir, warnings)
    cli_css_sanitized = (
        sanitize_css(options.cli_css, base_dir, options.allow_outside_base_dir)
        if options.cli_css.strip()
        else ""
    )

    has_mermaid = bool(mermaid_blocks)
    has_math = bool(math_spans)
    nonce = secrets.token_hex(16)

    raw_base_uri = Path(base_dir).resolve(strict=False).as_uri().rstrip("/") + "/"
    base_uri = html.escape(raw_base_uri, quote=True)
    safe_title = html.escape(options.title, quote=False)
    safe_lang = html.escape(effective_lang, quote=True)
    csp_content = (
        "default-src 'none'; "
        "img-src file: data:; "
        "style-src 'unsafe-inline' file:; "
        "font-src file: data:; "
        f"script-src 'nonce-{nonce}';"
    )

    head_parts: list[str] = [
        '<meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{csp_content}">',
        f'<base href="{base_uri}">',
        f"<title>{safe_title}</title>",
        f'<style id="mdtopdf-theme-css">\n{resolved_theme_css}\n</style>',
    ]
    if has_math:
        katex_css_uri = html.escape(
            (ASSETS_DIR / "katex" / "katex.min.css").resolve(strict=False).as_uri(),
            quote=True,
        )
        head_parts.append(f'<link rel="stylesheet" href="{katex_css_uri}">')
    if fm_css_sanitized:
        head_parts.append(f'<style id="mdtopdf-frontmatter-css">\n{fm_css_sanitized}\n</style>')
    if cli_css_sanitized:
        head_parts.append(f'<style id="mdtopdf-cli-css">\n{cli_css_sanitized}\n</style>')

    body_parts: list[str] = []
    if resolved_header_html:
        body_parts.append(resolved_header_html)
    body_parts.append(html_body)

    if has_mermaid:
        mermaid_js_uri = html.escape(
            (ASSETS_DIR / "mermaid.min.js").resolve(strict=False).as_uri(), quote=True
        )
        body_parts.append(f'<script nonce="{nonce}" src="{mermaid_js_uri}"></script>')
    if has_math:
        katex_js_uri = html.escape(
            (ASSETS_DIR / "katex" / "katex.min.js").resolve(strict=False).as_uri(), quote=True
        )
        body_parts.append(f'<script nonce="{nonce}" src="{katex_js_uri}"></script>')
    render_bridge_uri = html.escape(
        (ASSETS_DIR / "render_bridge.js").resolve(strict=False).as_uri(), quote=True
    )
    body_parts.append(f'<script nonce="{nonce}" src="{render_bridge_uri}"></script>')

    full_html = (
        f"<!DOCTYPE html>\n"
        f'<html lang="{safe_lang}">\n'
        f"<head>\n"
        + "\n".join(head_parts)
        + "\n</head>\n<body>\n"
        + "\n".join(body_parts)
        + "\n</body>\n</html>\n"
    )

    return BuiltDocument(
        html=full_html,
        suppressed_images=suppressed_images,
        warnings=warnings,
        has_mermaid=has_mermaid,
        has_math=has_math,
        nonce=nonce,
    )
