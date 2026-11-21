"""HTML sanitization, CSS sanitization, and base_dir local image containment."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

__all__ = [
    "ASSETS_DIR",
    "resolve_local_image",
    "sanitize_css",
    "sanitize_html",
]

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

_RAW_DROP_TAGS: frozenset[str] = frozenset({"script", "style"})
_VOID_TAGS: frozenset[str] = frozenset({"br", "hr", "img", "wbr", "col"})
_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "br",
        "hr",
        "blockquote",
        "pre",
        "code",
        "em",
        "strong",
        "b",
        "i",
        "u",
        "s",
        "del",
        "ins",
        "sub",
        "sup",
        "mark",
        "kbd",
        "samp",
        "var",
        "abbr",
        "cite",
        "q",
        "small",
        "time",
        "wbr",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "caption",
        "colgroup",
        "col",
        "a",
        "img",
        "div",
        "span",
        "figure",
        "figcaption",
        "details",
        "summary",
        "address",
    }
)

_GLOBAL_ATTRS: frozenset[str] = frozenset({"id", "class", "title", "dir", "lang", "style"})
_TAG_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "name", "rel", "target"}),
    "img": frozenset({"src", "alt", "width", "height"}),
    "ol": frozenset({"start", "reversed", "type"}),
    "li": frozenset({"value"}),
    "th": frozenset({"colspan", "rowspan", "align", "valign", "scope", "headers"}),
    "td": frozenset({"colspan", "rowspan", "align", "valign", "scope", "headers"}),
    "col": frozenset({"span", "align", "valign", "width"}),
    "colgroup": frozenset({"span", "align", "valign", "width"}),
    "table": frozenset({"summary"}),
    "blockquote": frozenset({"cite"}),
    "q": frozenset({"cite"}),
    "time": frozenset({"datetime"}),
    "details": frozenset({"open"}),
}

_SAFE_TAG_NAME_RE = re.compile(r"^[a-z][a-z0-9:-]*$")
_SAFE_ATTR_NAME_RE = re.compile(r"^[a-z_:][a-z0-9_.:-]*$")
_ENTITY_NAME_RE = re.compile(r"^[a-zA-Z0-9]+$")
_CHARREF_RE = re.compile(r"^(?:[0-9]+|[xX][0-9a-fA-F]+)$")

_URI_CTRL_WS_RE = re.compile(r"[\x00-\x20\x7f-\x9f\s]+")
_URI_SCHEME_CHECK_RE = re.compile(r"^([a-z][a-z0-9+.-]*):")
_SAFE_HREF_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
_SAFE_IMG_SCHEMES: frozenset[str] = frozenset({"file", "http", "https"})
_SAFE_DATA_IMAGE_RE = re.compile(
    r"^data:image/(?:png|jpe?g|gif|webp|bmp|x-icon|vnd\.microsoft\.icon|svg\+xml|tiff|avif)"
    r"(?:;[a-z0-9.+_-]+(?:=[^;,]*)?)*,",
    flags=re.IGNORECASE,
)

_LOCAL_URI_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]{1,}):")
_WIN_SLASH_DRIVE_RE = re.compile(r"^/([A-Za-z])[:|]/(.*)$")
_WIN_DRIVE_REL_RE = re.compile(r"^/?[A-Za-z]:(?![\\/])")
_WIN_DRIVE_ABS_RE = re.compile(r"^[A-Za-z]:[\\/]")

_CLOSING_STYLE_RE = re.compile(r"</\s*style\b[^>]*>?", flags=re.IGNORECASE)
_INLINE_CSS_HAZARD_RE = re.compile(
    r"(?:url\s*\(|(?<![a-zA-Z0-9_-])(?:-(?:webkit|moz)-)?image-set\s*\(|"
    r"(?<![a-zA-Z0-9_-])src\s*\(|expression\s*\(|behavior\s*:|-moz-binding\s*:|"
    r"@import\b|javascript:|vbscript:)",
    flags=re.IGNORECASE,
)
_INLINE_CSS_DECL_RE = re.compile(r"^[a-zA-Z-]+\s*:\s*[^;<>]+$")
_DATA_IMAGE_CSS_PROPS: frozenset[str] = frozenset({"background", "background-image"})
_HEX_CHARS = "0123456789abcdefABCDEF"


def _has_control_chars(value: str) -> bool:
    """Return True if value contains ASCII C0 controls or DEL."""
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _is_safe_data_image(value: str) -> bool:
    """Return True if value is an allowed inline raster/SVG image data URI."""
    if _has_control_chars(value):
        return False
    return _SAFE_DATA_IMAGE_RE.match(value) is not None


def _is_safe_href_uri(value: str, *, allow_mailto: bool) -> bool:
    """Return True if an href/cite URI is safe (http/https/mailto or relative/fragment)."""
    allowed_schemes = _SAFE_HREF_SCHEMES if allow_mailto else frozenset({"http", "https"})
    for candidate in (value, html.unescape(value)):
        normalized = _URI_CTRL_WS_RE.sub("", candidate).lower()
        match = _URI_SCHEME_CHECK_RE.match(normalized)
        if match is not None and match.group(1) not in allowed_schemes:
            return False
    return True


def _is_safe_img_src_uri(value: str) -> bool:
    """Allow only safe image schemes (file/http/https, valid data:image/*, or local paths)."""
    for candidate in (value, html.unescape(value)):
        normalized = _URI_CTRL_WS_RE.sub("", candidate).lower()
        match = _URI_SCHEME_CHECK_RE.match(normalized)
        if match is not None:
            scheme = match.group(1)
            if scheme == "data":
                if not _is_safe_data_image(candidate.strip()):
                    return False
            elif len(scheme) >= 2 and scheme not in _SAFE_IMG_SCHEMES:
                return False
    return True


def _scan_css_string_end(text: str, quote_start: int) -> int:
    """Return index immediately after closing quote (or len(text) if unclosed)."""
    quote = text[quote_start]
    i = quote_start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        i += 1
        if ch == quote:
            break
    return i


def _read_css_escape(text: str, backslash_idx: int) -> tuple[str, int]:
    """Decode one CSS backslash escape starting at ``backslash_idx``."""
    n = len(text)
    nxt = text[backslash_idx + 1]
    if nxt in "\r\n\f":
        is_crlf = nxt == "\r" and backslash_idx + 2 < n and text[backslash_idx + 2] == "\n"
        step = 3 if is_crlf else 2
        return ("", backslash_idx + step)

    if nxt in _HEX_CHARS:
        j = backslash_idx + 1
        while j < n and j - (backslash_idx + 1) < 6 and text[j] in _HEX_CHARS:
            j += 1
        codepoint = int(text[backslash_idx + 1 : j], 16)
        if j < n and text[j] in " \t\r\n\f":
            if text[j] == "\r" and j + 1 < n and text[j + 1] == "\n":
                j += 2
            else:
                j += 1
        decoded = chr(codepoint) if 1 <= codepoint <= 0x10FFFF else "\ufffd"
        return (decoded, j)

    return (nxt, backslash_idx + 2)


def _strip_css_comments(css_text: str) -> str:
    """Strip CSS /* ... */ comments outside quoted strings."""
    out: list[str] = []
    i = 0
    n = len(css_text)
    while i < n:
        ch = css_text[i]
        if ch in ("'", '"'):
            end_str = _scan_css_string_end(css_text, i)
            out.append(css_text[i:end_str])
            i = end_str
        elif ch == "/" and i + 1 < n and css_text[i + 1] == "*":
            end = css_text.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _decode_css_escapes(text: str) -> str:
    """Decode CSS backslash escapes (hex escapes and escaped punctuation)."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            decoded, i = _read_css_escape(text, i)
            out.append(decoded)
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _sanitize_inline_style(style_value: str) -> str:
    """Sanitize inline style attribute values while keeping safe layout rules (e.g. text-align)."""
    cleaned = _CLOSING_STYLE_RE.sub("", style_value).replace("<", "").replace(">", "")
    cleaned = _strip_css_comments(cleaned)
    safe_decls: list[str] = []
    for raw_decl in cleaned.split(";"):
        decl = raw_decl.strip()
        if not decl:
            continue
        decoded = _decode_css_escapes(decl)
        if _INLINE_CSS_HAZARD_RE.search(decoded) or _INLINE_CSS_HAZARD_RE.search(decl):
            continue
        if _INLINE_CSS_DECL_RE.fullmatch(decoded):
            safe_decls.append(decl)
    return "; ".join(safe_decls)


class _HtmlSanitizer(HTMLParser):
    """Allowlist-based HTML sanitizer built on stdlib HTMLParser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._raw_drop_tag: str | None = None

    def result(self) -> str:
        """Return sanitized HTML output."""
        return "".join(self._parts)

    def _filter_attrs(
        self, tag_lower: str, attrs: list[tuple[str, str | None]]
    ) -> list[tuple[str, str | None]]:
        allowed_for_tag = _GLOBAL_ATTRS | _TAG_ATTRS.get(tag_lower, frozenset())
        filtered: list[tuple[str, str | None]] = []
        seen_names: set[str] = set()

        for raw_name, raw_val in attrs:
            name_lower = raw_name.strip().lower()
            if not _SAFE_ATTR_NAME_RE.fullmatch(name_lower):
                continue
            if name_lower.startswith("on"):
                continue
            if name_lower not in allowed_for_tag:
                continue

            if name_lower == "href":
                if raw_val is None or not _is_safe_href_uri(raw_val, allow_mailto=True):
                    continue
            elif name_lower == "cite":
                if raw_val is None or not _is_safe_href_uri(raw_val, allow_mailto=False):
                    continue
            elif name_lower == "src":
                if raw_val is None or not _is_safe_img_src_uri(raw_val):
                    continue
            elif name_lower == "style":
                if raw_val is None:
                    continue
                safe_style = _sanitize_inline_style(raw_val)
                if not safe_style:
                    continue
                raw_val = safe_style

            if name_lower in seen_names:
                continue
            seen_names.add(name_lower)
            filtered.append((name_lower, raw_val))

        return filtered

    def _emit_start(
        self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool
    ) -> None:
        tag_lower = tag.strip().lower()
        if not _SAFE_TAG_NAME_RE.fullmatch(tag_lower) or tag_lower not in _ALLOWED_TAGS:
            return
        safe_attrs = self._filter_attrs(tag_lower, attrs)
        attr_text = ""
        if safe_attrs:
            pieces: list[str] = []
            for name, val in safe_attrs:
                if val is None:
                    pieces.append(name)
                else:
                    pieces.append(f'{name}="{html.escape(val, quote=True)}"')
            attr_text = " " + " ".join(pieces)

        if self_closing and tag_lower in _VOID_TAGS:
            self._parts.append(f"<{tag_lower}{attr_text} />")
        else:
            self._parts.append(f"<{tag_lower}{attr_text}>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._raw_drop_tag is not None:
            return
        tag_lower = tag.strip().lower()
        if tag_lower in _RAW_DROP_TAGS:
            self._raw_drop_tag = tag_lower
            return
        self._emit_start(tag_lower, attrs, self_closing=tag_lower in _VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._raw_drop_tag is not None:
            return
        tag_lower = tag.strip().lower()
        if tag_lower in _RAW_DROP_TAGS:
            return
        self._emit_start(tag_lower, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.strip().lower()
        if self._raw_drop_tag is not None:
            if tag_lower == self._raw_drop_tag:
                self._raw_drop_tag = None
            return
        if (
            _SAFE_TAG_NAME_RE.fullmatch(tag_lower)
            and tag_lower in _ALLOWED_TAGS
            and tag_lower not in _VOID_TAGS
        ):
            self._parts.append(f"</{tag_lower}>")

    def handle_data(self, data: str) -> None:
        if self._raw_drop_tag is not None:
            return
        self._parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if self._raw_drop_tag is not None:
            return
        if _ENTITY_NAME_RE.fullmatch(name):
            self._parts.append(f"&{name};")
        else:
            self._parts.append(html.escape(f"&{name};", quote=False))

    def handle_charref(self, name: str) -> None:
        if self._raw_drop_tag is not None:
            return
        if _CHARREF_RE.fullmatch(name):
            self._parts.append(f"&#{name};")
        else:
            self._parts.append(html.escape(f"&#{name};", quote=False))

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


def sanitize_html(html_body: str) -> str:
    """Sanitize compiled HTML using an allowlist HTMLParser filter.

    Strips dangerous tags (``script``, ``style``, ``iframe``, ``object``, ``embed``,
    ``applet``, ``meta``, ``link``, ``base``, ``frame``, ``frameset``, ``form``,
    ``input``, ``svg``), comments/PI/DOCTYPE/CDATA, ``on*`` event handlers,
    ``srcdoc``, and unsafe URI schemes while preserving safe Markdown HTML.
    """
    if not html_body:
        return ""
    parser = _HtmlSanitizer()
    parser.feed(html_body)
    parser.close()
    return parser.result()


def resolve_local_image(
    src: str,
    base_dir: str,
    allow_outside_base_dir: bool = False,
) -> tuple[bool, bool, Path | None]:
    """Validate and resolve an image source against ``base_dir``.

    Returns ``(allowed, exists, resolved_path)``:
    - Existing allowed local file: ``(True, True, Path)``
    - Missing allowed local file: ``(True, False, Path)``
    - Safe inline ``data:image/*`` URI: ``(True, True, None)``
    - Blocked (``http(s):``, UNC ``\\\\``/``//``, ``file://host``, unsafe scheme,
      empty/control chars, or outside ``base_dir`` when disallowed): ``(False, False, None)``
    """
    if not src or not src.strip():
        return (False, False, None)

    raw = src.strip()
    if _has_control_chars(raw):
        return (False, False, None)

    if raw.lower().startswith("data:"):
        if _is_safe_data_image(raw):
            return (True, True, None)
        return (False, False, None)

    if raw.startswith("//") or raw.startswith("\\\\"):
        return (False, False, None)

    scheme_match = _LOCAL_URI_SCHEME_RE.match(raw)
    if scheme_match is not None:
        scheme = scheme_match.group(1).lower()
        if scheme != "file":
            return (False, False, None)
        parsed = urlparse(raw)
        if parsed.netloc.lower() not in {"", "localhost"}:
            return (False, False, None)
        decoded_path = unquote(parsed.path)
        if _has_control_chars(decoded_path):
            return (False, False, None)
        if (
            decoded_path.startswith("//")
            or decoded_path.startswith("\\\\")
            or decoded_path.startswith("/\\\\")
        ):
            return (False, False, None)
        win_drive = _WIN_SLASH_DRIVE_RE.match(decoded_path)
        if win_drive is not None:
            decoded_path = f"{win_drive.group(1)}:/{win_drive.group(2)}"
        if _WIN_DRIVE_REL_RE.match(decoded_path):
            return (False, False, None)
        candidate_str = decoded_path
    else:
        decoded_path = unquote(raw)
        if _has_control_chars(decoded_path):
            return (False, False, None)
        if decoded_path.startswith("//") or decoded_path.startswith("\\\\"):
            return (False, False, None)
        if _WIN_DRIVE_REL_RE.match(decoded_path):
            return (False, False, None)
        candidate_str = decoded_path

    if not candidate_str:
        return (False, False, None)

    if not Path(candidate_str).is_absolute() and not _WIN_DRIVE_ABS_RE.match(candidate_str):
        candidate_str = candidate_str.replace("\\", "/")

    try:
        base_path = Path(base_dir).resolve(strict=False)
        raw_path = Path(candidate_str)
        candidate = (raw_path if raw_path.is_absolute() else (base_path / raw_path)).resolve(
            strict=False
        )
        if not allow_outside_base_dir and not candidate.is_relative_to(base_path):
            return (False, False, None)
        exists = candidate.is_file()
    except (OSError, ValueError):
        return (False, False, None)

    return (True, exists, candidate)


def _read_css_ident(text: str, start: int) -> tuple[str, int]:
    """Read a CSS identifier or keyword starting at ``start``, decoding CSS escapes."""
    n = len(text)
    i = start
    parts: list[str] = []
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            if text[i + 1] in "\r\n\f":
                break
            decoded, i = _read_css_escape(text, i)
            parts.append(decoded)
        elif ch.isalnum() or ch in ("-", "_"):
            parts.append(ch)
            i += 1
        else:
            break
    return ("".join(parts), i)


def _skip_css_parens(text: str, start_after_open: int) -> int:
    """Advance past balanced parentheses starting right after '('."""
    depth = 1
    i = start_after_open
    n = len(text)
    while i < n and depth > 0:
        ch = text[i]
        if ch in ("'", '"'):
            i = _scan_css_string_end(text, i)
        elif ch == "\\" and i + 1 < n:
            i += 2
        elif ch == "(":
            depth += 1
            i += 1
        elif ch == ")":
            depth -= 1
            i += 1
        else:
            i += 1
    return i


def _skip_css_rule_or_decl(text: str, start: int, *, stop_on_open_brace: bool) -> int:
    """Advance past a CSS @import rule or dangerous property declaration."""
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            i = _scan_css_string_end(text, i)
        elif ch == "(":
            i = _skip_css_parens(text, i + 1)
        elif ch == ";":
            return i + 1
        elif ch == "}" or (stop_on_open_brace and ch == "{"):
            return i
        else:
            i += 1
    return n


def _parse_css_url_arg(text: str, start_after_open: int) -> tuple[str, int]:
    """Parse quoted or unquoted url(...) argument starting right after '('."""
    n = len(text)
    i = start_after_open
    while i < n and text[i].isspace():
        i += 1
    if i >= n:
        return ("", n)

    if text[i] in ("'", '"'):
        end_str = _scan_css_string_end(text, i)
        inner = text[i + 1 : end_str - 1] if text[end_str - 1] == text[i] else text[i + 1 : end_str]
        i = end_str
        while i < n and text[i].isspace():
            i += 1
        if i < n and text[i] == ")":
            i += 1
        else:
            i = _skip_css_parens(text, i)
        return (_decode_css_escapes(inner).strip(), i)

    buf: list[str] = []
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(text[i + 1])
            i += 2
            continue
        if ch == ")":
            i += 1
            break
        buf.append(ch)
        i += 1
    return (_decode_css_escapes("".join(buf)).strip(), i)


def _is_abs_or_file_target(target: str) -> bool:
    """Return True if target is an explicit file:/// URI or absolute path (not relative)."""
    lowered = target.lower()
    if lowered.startswith("file:"):
        return True
    if _WIN_DRIVE_ABS_RE.match(target):
        return True
    return target.startswith("/") and not target.startswith("//")


def _validate_css_url(
    target: str,
    current_prop: str,
    base_dir: str,
    allow_outside_base_dir: bool,
) -> str | None:
    """Validate a decoded CSS url(...) target and return a safe rewritten URI or None."""
    if not target or _has_control_chars(target):
        return None
    if target.lower().startswith("data:"):
        if current_prop in _DATA_IMAGE_CSS_PROPS and _is_safe_data_image(target):
            return target
        return None

    allowed, _, resolved_path = resolve_local_image(
        target,
        base_dir,
        allow_outside_base_dir=allow_outside_base_dir,
    )
    if allowed and resolved_path is not None:
        return resolved_path.as_uri()

    if _is_abs_or_file_target(target):
        asset_allowed, _, asset_path = resolve_local_image(
            target,
            str(ASSETS_DIR),
            allow_outside_base_dir=False,
        )
        if asset_allowed and asset_path is not None:
            return asset_path.as_uri()

    return None


def sanitize_css(
    css_text: str,
    base_dir: str,
    allow_outside_base_dir: bool = False,
) -> str:
    """Sanitize CSS against HTML breakout, dangerous directives, and external url() targets.

    Strips ``</style>``, ``<``, ``>``, CSS comments, ``@import``, ``expression(...)``,
    ``behavior:``, ``-moz-binding:``, and any ``url(...)`` (quoted, unquoted, or
    CSS-escaped) whose decoded target does not resolve inside ``base_dir`` (unless
    ``allow_outside_base_dir=True``) or bundled ``assets/``, except ``data:image/*`` in
    ``background`` / ``background-image``.
    """
    if not css_text:
        return ""

    stripped = _CLOSING_STYLE_RE.sub("", css_text).replace("<", "").replace(">", "")
    no_comments = _strip_css_comments(stripped)

    out: list[str] = []
    i = 0
    n = len(no_comments)
    at_decl_start = True
    current_prop = ""
    pending_ident = ""

    while i < n:
        ch = no_comments[i]

        if ch in ("'", '"'):
            end_str = _scan_css_string_end(no_comments, i)
            out.append(no_comments[i:end_str])
            i = end_str
            continue

        if ch in ("{", "}", ";"):
            out.append(ch)
            at_decl_start = True
            current_prop = ""
            pending_ident = ""
            i += 1
            continue

        if ch == ":":
            if at_decl_start and pending_ident:
                current_prop = pending_ident.lower()
                at_decl_start = False
            out.append(ch)
            pending_ident = ""
            i += 1
            continue

        if ch == "@":
            kw, next_i = _read_css_ident(no_comments, i + 1)
            if kw.lower() == "import":
                i = _skip_css_rule_or_decl(no_comments, next_i, stop_on_open_brace=True)
                at_decl_start = True
                current_prop = ""
                pending_ident = ""
                continue
            out.append("@")
            out.append(no_comments[i + 1 : next_i])
            i = next_i
            continue

        if ch.isalpha() or ch in ("-", "_") or (ch == "\\" and i + 1 < n):
            ident, next_i = _read_css_ident(no_comments, i)
            ident_lower = ident.lower()

            if ident_lower in {
                "expression",
                "image-set",
                "-webkit-image-set",
                "-moz-image-set",
                "src",
            }:
                ws_i = next_i
                while ws_i < n and no_comments[ws_i].isspace():
                    ws_i += 1
                if ws_i < n and no_comments[ws_i] == "(":
                    i = _skip_css_parens(no_comments, ws_i + 1)
                    continue

            if ident_lower == "url" and next_i < n and no_comments[next_i] == "(":
                target, after_url = _parse_css_url_arg(no_comments, next_i + 1)
                safe_uri = _validate_css_url(target, current_prop, base_dir, allow_outside_base_dir)
                if safe_uri is not None:
                    escaped_uri = safe_uri.replace("\\", "%5C").replace('"', "%22")
                    out.append(f'url("{escaped_uri}")')
                i = after_url
                continue

            if at_decl_start and ident_lower in {"behavior", "-moz-binding"}:
                ws_i = next_i
                while ws_i < n and no_comments[ws_i].isspace():
                    ws_i += 1
                if ws_i < n and no_comments[ws_i] == ":":
                    i = _skip_css_rule_or_decl(no_comments, ws_i + 1, stop_on_open_brace=False)
                    current_prop = ""
                    pending_ident = ""
                    continue

            if ident_lower:
                pending_ident = ident_lower
                out.append(no_comments[i:next_i])
                i = next_i
                continue

        out.append(ch)
        i += 1

    result = "".join(out)
    return _CLOSING_STYLE_RE.sub("", result).replace("<", "").replace(">", "")
