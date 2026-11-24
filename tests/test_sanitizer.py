"""Unit tests for mdtopdf_skill.sanitizer."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdtopdf_skill.sanitizer import (
    ASSETS_DIR,
    resolve_local_image,
    sanitize_css,
    sanitize_html,
)


class TestSanitizeHtmlTagsAndStructure:
    """Verify dangerous tag stripping and safe Markdown HTML preservation."""

    def test_empty_input(self) -> None:
        assert sanitize_html("") == ""

    def test_preserves_safe_markdown_html(self) -> None:
        safe_html = (
            '<h1 id="title">Heading</h1>'
            '<p>Text with <strong>bold</strong>, <em>em</em>, <code>x &lt; y</code>, '
            '&amp; &#x27; &#39;.</p>'
            '<table><thead><tr><th style="text-align: left;">Col</th></tr></thead>'
            '<tbody><tr><td style="text-align: center;" colspan="1">Val</td></tr></tbody></table>'
            '<p><a href="https://example.com" title="Site">link</a> '
            '<sup id="fnref:1"><a class="footnote-ref" href="#fn:1">1</a></sup></p>'
            '<img src="file:///tmp/pic.png" alt="Pic" width="100" height="50" />'
            '<div class="station-block"><h3>Station</h3><ul><li>A</li></ul></div>'
            "<br /><hr />"
        )
        result = sanitize_html(safe_html)
        assert '<h1 id="title">Heading</h1>' in result
        assert '<th style="text-align: left">Col</th>' in result
        assert '<td style="text-align: center" colspan="1">Val</td>' in result
        assert '<a href="https://example.com" title="Site">link</a>' in result
        assert 'href="#fn:1"' in result
        assert 'src="file:///tmp/pic.png"' in result
        assert "&amp; &#x27; &#39;" in result
        assert "<br /><hr />" in result

    @pytest.mark.parametrize(
        "payload",
        [
            "<script>alert(1)</script><p>ok</p>",
            "<SCRIPT SRC='evil.js'>alert(1)</SCRIPT><p>ok</p>",
            "<script/>alert(1)</script><p>ok</p>",
            "<script>unclosed()",
            "<style>body{background:url(https://evil.com)}</style><p>ok</p>",
            "<style/><p>ok</p>",
            '<iframe src="https://evil.com">fallback</iframe><p>ok</p>',
            '<object data="evil.swf"></object><p>ok</p>',
            '<embed src="evil.swf" /><p>ok</p>',
            '<applet code="Evil.class"></applet><p>ok</p>',
            '<meta http-equiv="refresh" content="0;url=evil"><p>ok</p>',
            '<link rel="stylesheet" href="https://evil.com/a.css"><p>ok</p>',
            '<base href="https://evil.com/"><p>ok</p>',
            '<frame src="evil.html"><frameset></frameset><p>ok</p>',
            '<form action="https://evil.com"><input type="text"></form><p>ok</p>',
            '<svg onload="alert(1)"><script>alert(2)</script><circle/></svg><p>ok</p>',
        ],
    )
    def test_strips_dangerous_tags(self, payload: str) -> None:
        result = sanitize_html(payload)
        for forbidden in (
            "<script",
            "<style",
            "<iframe",
            "<object",
            "<embed",
            "<applet",
            "<meta",
            "<link",
            "<base",
            "<frame",
            "<frameset",
            "<form",
            "<input",
            "<svg",
            "onload",
        ):
            assert forbidden not in result.lower()

    def test_script_drops_nested_entities_charrefs_and_subtags(self) -> None:
        payload = "<script>var a = '&amp;&#x27;<b>1</b>';</script><p>after</p>"
        result = sanitize_html(payload)
        assert result == "<p>after</p>"

    def test_raw_drop_mode_ignores_non_matching_end_tag(self) -> None:
        payload = "<script>var x = '</div>';</script><p>safe</p>"
        assert sanitize_html(payload) == "<p>safe</p>"

    def test_strips_comments_pi_decl_cdata_and_escapes_broken_tags(self) -> None:
        payload = (
            "<!-- secret comment -->"
            "<?xml version='1.0'?>"
            "<!DOCTYPE html>"
            "<![CDATA[raw cdata]]>"
            "<p>1 < 2 &amp; 3 > 1</p>"
            '<div <script>hello</div>'
        )
        result = sanitize_html(payload)
        assert "secret comment" not in result
        assert "xml" not in result
        assert "DOCTYPE" not in result
        assert "raw cdata" not in result
        assert "1 &lt; 2 &amp; 3 &gt; 1" in result
        assert "<script" not in result.lower()

    def test_rejects_malformed_entity_and_charref_names(self) -> None:
        parser_probe = sanitize_html("<p>&bad_name; &#bad;</p>")
        assert "&bad;_name;" in parser_probe
        assert "&amp;#bad;" in parser_probe

    def test_void_end_tags_and_invalid_tag_names_ignored(self) -> None:
        result = sanitize_html("<p>a</br></img><1bad>x</1bad></p>")
        assert result == "<p>a&lt;1bad&gt;x</p>"

    def test_html_parser_raw_drop_direct_methods(self) -> None:
        from mdtopdf_skill.sanitizer import _HtmlSanitizer

        san = _HtmlSanitizer()
        san.handle_starttag("script", [])
        san.handle_starttag("b", [])
        san.handle_startendtag("br", [])
        san.handle_entityref("amp")
        san.handle_charref("65")
        san.handle_endtag("b")
        san.handle_endtag("script")
        san.handle_entityref("bad_entity")
        san.handle_charref("bad_char")
        assert san.result() == "&amp;bad_entity;&amp;#bad_char;"


class TestSanitizeHtmlAttributesAndUris:
    """Verify attribute allowlisting, on* stripping, URI checks, and inline style filtering."""

    def test_strips_event_handlers_srcdoc_and_disallowed_attrs(self) -> None:
        html_in = (
            '<p onclick="alert(1)" ONLOAD="evil()" on="1" srcdoc="<script>1</script>" '
            'data-custom="x" id="p1" id="p2">Hi</p>'
            '<details open><summary>More</summary></details>'
        )
        result = sanitize_html(html_in)
        assert "onclick" not in result.lower()
        assert "onload" not in result.lower()
        assert "srcdoc" not in result.lower()
        assert "data-custom" not in result.lower()
        assert result == '<p id="p1">Hi</p><details open><summary>More</summary></details>'

    @pytest.mark.parametrize(
        "href",
        [
            "javascript:alert(1)",
            "  JaVaScRiPt:alert(1)",
            "java&#x09;script:alert(1)",
            "java&#0000115cript:alert(1)",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "data:text/html,<script>alert(1)</script>",
        ],
    )
    def test_blocks_unsafe_href_schemes(self, href: str) -> None:
        result = sanitize_html(f'<a href="{href}">click</a>')
        assert result == "<a>click</a>"

    def test_duplicate_href_blocks_first_dangerous_keeps_safe(self) -> None:
        result = sanitize_html('<a href="javascript:alert(1)" href="https://safe.example">ok</a>')
        assert result == '<a href="https://safe.example">ok</a>'

    def test_blocks_mailto_in_cite_while_allowing_in_href(self) -> None:
        assert (
            sanitize_html('<a href="mailto:user@example.com">mail</a>')
            == '<a href="mailto:user@example.com">mail</a>'
        )
        assert sanitize_html('<blockquote cite="mailto:a@b.com">q</blockquote>') == (
            "<blockquote>q</blockquote>"
        )
        assert sanitize_html('<blockquote cite="https://example.com">q</blockquote>') == (
            '<blockquote cite="https://example.com">q</blockquote>'
        )

    def test_blocks_boolean_href_cite_src_style_and_unsafe_img_src(self) -> None:
        assert sanitize_html("<a href>x</a>") == "<a>x</a>"
        assert sanitize_html("<q cite>x</q>") == "<q>x</q>"
        assert sanitize_html("<img src>") == "<img />"
        assert sanitize_html("<p style>x</p>") == "<p>x</p>"
        assert sanitize_html('<img src="javascript:alert(1)" alt="a">') == '<img alt="a" />'
        assert sanitize_html('<img src="vbscript:evil()" alt="b">') == '<img alt="b" />'
        assert sanitize_html('<img src="data:text/html,<b>x</b>" alt="c">') == '<img alt="c" />'
        assert sanitize_html('<img src="data:image/png;base64,\x07" alt="d">') == '<img alt="d" />'
        assert (
            sanitize_html('<img src="data:image/png;base64,iVBORw0KGgo=" alt="ok">')
            == '<img src="data:image/png;base64,iVBORw0KGgo=" alt="ok" />'
        )

    def test_inline_style_preserves_safe_decls_and_strips_hazards(self) -> None:
        safe = sanitize_html('<th style="text-align: right; color: #333;">H</th>')
        assert safe == '<th style="text-align: right; color: #333">H</th>'

        unsafe = sanitize_html(
            '<div style="background: url(https://evil.com/a.png); '
            'width: expression(alert(1)); behavior: url(x.htc); -moz-binding: url(x); '
            'color: red;">Box</div>'
        )
        assert "url(" not in unsafe.lower()
        assert "expression" not in unsafe.lower()
        assert "behavior" not in unsafe.lower()
        assert "-moz-binding" not in unsafe.lower()
        assert 'style="color: red"' in unsafe

        only_bad = sanitize_html('<p style="background: url(x.png); ; bad_no_colon">T</p>')
        assert only_bad == "<p>T</p>"

    def test_ignores_malformed_attribute_names(self) -> None:
        result = sanitize_html('<div <script="1" class="ok">x</div>')
        assert result == '<div class="ok">x</div>'


class TestResolveLocalImage:
    """Verify resolve_local_image (allowed, exists, resolved_path) contract."""

    def test_existing_and_missing_local_images(self, tmp_path: Path) -> None:
        img = tmp_path / "sub" / "pic.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"PNG")

        allowed, exists, resolved = resolve_local_image("sub/pic.png", str(tmp_path))
        assert (allowed, exists, resolved) == (True, True, img.resolve())

        allowed_m, exists_m, resolved_m = resolve_local_image("sub/missing.png", str(tmp_path))
        expected_missing = (tmp_path / "sub/missing.png").resolve()
        assert (allowed_m, exists_m, resolved_m) == (True, False, expected_missing)

    def test_percent_encoded_and_backslash_relative_paths(self, tmp_path: Path) -> None:
        img = tmp_path / "img folder" / "รูป.png"
        img.parent.mkdir(parents=True)
        img.write_bytes(b"PNG")

        encoded = "img%20folder/%E0%B8%A3%E0%B8%B9%E0%B8%9B.png"
        assert resolve_local_image(encoded, str(tmp_path)) == (
            True,
            True,
            img.resolve(),
        )
        assert resolve_local_image(r"img folder\รูป.png", str(tmp_path)) == (
            True,
            True,
            img.resolve(),
        )

    def test_file_uri_local_and_localhost(self, tmp_path: Path) -> None:
        img = tmp_path / "a.png"
        img.write_bytes(b"PNG")

        assert resolve_local_image(img.as_uri(), str(tmp_path)) == (True, True, img.resolve())
        localhost_uri = img.as_uri().replace("file:///", "file://localhost/")
        assert resolve_local_image(localhost_uri, str(tmp_path)) == (True, True, img.resolve())

    def test_data_image_uris(self, tmp_path: Path) -> None:
        assert resolve_local_image("data:image/png;base64,iVBORw0KGgo=", str(tmp_path)) == (
            True,
            True,
            None,
        )
        assert resolve_local_image("data:image/svg+xml;utf8,<svg></svg>", str(tmp_path)) == (
            True,
            True,
            None,
        )
        assert resolve_local_image("data:text/html,<script>1</script>", str(tmp_path)) == (
            False,
            False,
            None,
        )
        assert resolve_local_image("data:image/png;base64,\x00", str(tmp_path)) == (
            False,
            False,
            None,
        )

    def test_traversal_and_allow_outside_base_dir(self, tmp_path: Path) -> None:
        base = tmp_path / "doc"
        base.mkdir()
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"PNG")

        assert resolve_local_image("../outside.png", str(base)) == (False, False, None)
        assert resolve_local_image("%2e%2e/outside.png", str(base)) == (False, False, None)
        assert resolve_local_image("../outside.png", str(base), allow_outside_base_dir=True) == (
            True,
            True,
            outside.resolve(),
        )

    @pytest.mark.parametrize(
        "blocked_src",
        [
            "",
            "   ",
            "pic\x00.png",
            "pic%00.png",
            "http://example.com/a.png",
            "https://example.com/a.png",
            "//evil.example/share/a.png",
            "%2f%2fevil.example/share/a.png",
            r"\\server\share\a.png",
            r"\\?\C:\Users\a.png",
            "file://evilserver/share/a.png",
            "file://///evilserver/share/a.png",
            r"file:///\\evilserver\share\a.png",
            "file:///C:/a%00.png",
            "file:///C:rel.png",
            "file://",
            "C:rel.png",
            "javascript:alert(1)",
        ],
    )
    def test_always_blocks_remote_unc_and_malformed_uris(
        self, tmp_path: Path, blocked_src: str
    ) -> None:
        assert resolve_local_image(blocked_src, str(tmp_path), allow_outside_base_dir=True) == (
            False,
            False,
            None,
        )

    def test_file_uri_windows_slash_drive_normalization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allowed, exists, resolved = resolve_local_image(
            "file:///Z|/outside/pic.png", str(tmp_path), allow_outside_base_dir=False
        )
        assert (allowed, exists, resolved) == (False, False, None)

        monkeypatch.setattr("mdtopdf_skill.sanitizer.sys.platform", "linux")
        assert resolve_local_image(
            "file:///Z|/outside/pic.png", str(tmp_path), allow_outside_base_dir=False
        ) == (False, False, None)
        assert resolve_local_image(
            "C:/outside/x.png", str(tmp_path), allow_outside_base_dir=False
        ) == (False, False, None)
        assert "outside" not in sanitize_css(
            'a { background: url("C:/outside/x.png"); }', str(tmp_path)
        )

    def test_os_error_during_resolution_returns_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(self: Path, strict: bool = False) -> Path:
            raise OSError("simulated failure")

        monkeypatch.setattr(Path, "resolve", _boom)
        assert resolve_local_image("pic.png", str(tmp_path)) == (False, False, None)


class TestSanitizeCss:
    """Verify CSS sanitization against breakouts, directives, escapes, and external urls."""

    def test_empty_and_safe_css(self, tmp_path: Path) -> None:
        assert sanitize_css("", str(tmp_path)) == ""
        safe = (
            'body { font-family: "Noto Sans", sans-serif; color: #111; } '
            'li::before { content: "\\2022"; } '
            "@media print { h1 { page-break-after: avoid; } }"
        )
        assert sanitize_css(safe, str(tmp_path)) == safe

    def test_strips_style_breakout_and_angle_brackets(self, tmp_path: Path) -> None:
        css = 'body { color: red; } /* </style><script>alert(1)</script> */ p { margin: 0; }'
        result = sanitize_css(css, str(tmp_path))
        assert "</style" not in result.lower()
        assert "<" not in result
        assert ">" not in result
        assert "color: red;" in result
        assert "margin: 0;" in result

    def test_strips_comments_and_handles_unclosed_comment_and_string(self, tmp_path: Path) -> None:
        css = 'p { color: red; } /* unclosed comment'
        assert sanitize_css(css, str(tmp_path)) == "p { color: red; } "
        css2 = r'p { content: "a\"b"; } /* c */'
        assert 'content: "a\\"b";' in sanitize_css(css2, str(tmp_path))
        css3 = 'p { content: "unclosed'
        assert sanitize_css(css3, str(tmp_path)) == 'p { content: "unclosed'

    @pytest.mark.parametrize(
        "malicious_css",
        [
            '@import "https://evil.com/a.css"; body { color: black; }',
            '@import url("https://evil.com/a.css"); body { color: black; }',
            "@IM/*x*/PORT 'evil.css'; body { color: black; }",
            r"@\69mport 'evil.css'; body { color: black; }",
            r"@\000069 mport url('evil.css'); body { color: black; }",
            '@import "unclosed.css"',
        ],
    )
    def test_strips_import_rules(self, tmp_path: Path, malicious_css: str) -> None:
        result = sanitize_css(malicious_css, str(tmp_path))
        assert "import" not in result.lower()
        assert "evil.css" not in result.lower()

    def test_import_followed_by_rule_block_preserves_rule(self, tmp_path: Path) -> None:
        css = '@import "x.css" { } body { color: blue; }'
        result = sanitize_css(css, str(tmp_path))
        assert "import" not in result.lower()
        assert "color: blue;" in result

    @pytest.mark.parametrize(
        "malicious_css",
        [
            "p { width: expression(alert(1)); color: red; }",
            "p { width: exp/*x*/ression(a(1) + b(')')); color: red; }",
            r"p { width: \65xpression(alert(1)); color: red; }",
            "p { width: expression (alert(1)); color: red; }",
            "p { behavior: url(evil.htc); color: red; }",
            "p { behav/*x*/ior: 'evil.htc'; color: red; }",
            "p { -moz-binding: url(evil.xml#xss); color: red; }",
            "p { -moz-binding: ('x') }",
        ],
    )
    def test_strips_expression_behavior_and_moz_binding(
        self, tmp_path: Path, malicious_css: str
    ) -> None:
        result = sanitize_css(malicious_css, str(tmp_path))
        assert "expression" not in result.lower()
        assert "behavior" not in result.lower()
        assert "-moz-binding" not in result.lower()

    def test_url_validation_local_assets_data_and_blocked(self, tmp_path: Path) -> None:
        local_img = tmp_path / "bg.png"
        local_img.write_bytes(b"PNG")
        outside_img = tmp_path.parent / "outside_bg.png"
        outside_img.write_bytes(b"PNG")

        # Allowed local path (quoted, single-quoted, unquoted, CSS-escaped url keyword)
        css_local = (
            'a { background: url("bg.png"); } '
            "b { background-image: url('bg.png'); } "
            "c { background: url(bg.png); } "
            r"d { background: \75\72\6c('bg.png'); }"
        )
        res_local = sanitize_css(css_local, str(tmp_path))
        assert res_local.count(local_img.resolve().as_uri()) == 4

        # Allowed bundled asset URI
        katex_font = ASSETS_DIR / "katex" / "fonts" / "KaTeX_Main-Regular.woff2"
        res_asset = sanitize_css(
            f'@font-face {{ src: url("{katex_font.as_uri()}"); }}',
            str(tmp_path),
        )
        assert katex_font.resolve().as_uri() in res_asset

        # Allowed data:image/* in background / background-image ONLY
        res_data_ok = sanitize_css(
            "div { background: url(data:image/png;base64,iVBORw0KGgo=); "
            "background-image: url('data:image/gif;base64,R0lGOD=='); }",
            str(tmp_path),
        )
        assert "data:image/png;base64,iVBORw0KGgo=" in res_data_ok
        assert "data:image/gif;base64,R0lGOD==" in res_data_ok

        # Blocked data:image/* in non-background property, and blocked data:text/html
        res_data_bad = sanitize_css(
            "div { content: url(data:image/png;base64,iVBORw0KGgo=); "
            "background: url(data:text/html,<script>1</script>); }",
            str(tmp_path),
        )
        assert "data:" not in res_data_bad

        # Blocked external/UNC/outside URLs (including CSS-escaped target)
        res_blocked = sanitize_css(
            "a { background: url(https://evil.com/a.png); } "
            "b { background: url(//evil.com/a.png); } "
            r"c { background: url(\\server\share\a.png); } "
            "d { background: url(file://evilhost/a.png); } "
            'e { background: url("../outside_bg.png"); } '
            r'f { background: url("\68ttps://evil.com/b.png"); }',
            str(tmp_path),
        )
        assert "url(" not in res_blocked

        # allow_outside_base_dir=True permits outside local path while still blocking http/UNC
        res_outside = sanitize_css(
            'a { background: url("../outside_bg.png"); } '
            "b { background: url(https://evil.com/a.png); }",
            str(tmp_path),
            allow_outside_base_dir=True,
        )
        assert outside_img.resolve().as_uri() in res_outside
        assert "evil.com" not in res_outside

    def test_css_edge_cases_escapes_and_malformed_urls(self, tmp_path: Path) -> None:
        # Line continuation inside CSS identifier/value, out-of-range hex escape, malformed url()
        css = (
            "p { color: \\110000; } "
            "q { background: url(   ); } "
            "r { background: url('bg.png' extra); } "
            "s { background: url(\\\"bg.png\\\"); } "
            "t { background: url(\n); } "
            "u { background: url(\r\n); } "
            "v { foo\\:bar: baz; } "
            "w { content: '\\\nhello'; }"
        )
        result = sanitize_css(css, str(tmp_path))
        assert isinstance(result, str)

        # Unclosed url( and CSS escape followed by CRLF in ident
        css_unclosed = "a { background: url(http://x) } b { \\61\\\r\n: 1; } c { \\61\r\nb: 1; }"
        res_unclosed = sanitize_css(css_unclosed, str(tmp_path))
        assert "http://" not in res_unclosed

        # Escaped parens inside unquoted url() and malformed url('') without closing )
        css_esc_url = r"a { background: url(sub\)pic.png); } b { background: url('x.png'; }"
        res_esc = sanitize_css(css_esc_url, str(tmp_path))
        assert "sub%29pic.png" in res_esc

        # Trailing backslash or empty url( and whitespace-only url(
        assert sanitize_css("a { background: url(", str(tmp_path)) == "a { background: "
        assert sanitize_css("a { background: url(   ", str(tmp_path)) == "a { background: "
        assert sanitize_css("a { content: \\", str(tmp_path)) == "a { content: \\"

        # CRLF after hex escape in CSS ident, line continuations, parens escape, space before :
        res_crlf = sanitize_css(
            'a { \\63\r\nolor: red; background: url("C:/outside/x.png"); '
            'background: url("b\\\r\ng.png"); background: url("b\\\ng.png"); '
            "width: expression(a(\\))); behavior : url(evil.htc); }",
            str(tmp_path),
        )
        assert "olor: red;" in res_crlf
        assert "bg.png" in res_crlf
        assert "outside" not in res_crlf
        assert "expression" not in res_crlf
        assert "behavior" not in res_crlf


class TestSecurityAuditRegressions:
    """Regression tests from Task 6 security audit covering sanitizer bypass vectors."""

    def test_blocks_css_url_backslash_string_breakout(self, tmp_path: Path) -> None:
        outside = (tmp_path.parent / "secret.png").resolve().as_uri()
        malicious_css = (
            "div { background: url('data:image/png;base64,A\\\\'); "
            f'x: "); }} body {{ background-image: url({outside}); }} /*"; }}'
        )
        result = sanitize_css(malicious_css, str(tmp_path))
        assert 'url("data:image/png;base64,A%5C")' in result
        assert r'\"' not in result

        direct_css = (
            "div { background: url('data:image/png;base64,A\\\\'); } "
            f"body {{ background-image: url({outside}); }}"
        )
        direct_result = sanitize_css(direct_css, str(tmp_path))
        assert 'url("data:image/png;base64,A%5C")' in direct_result
        assert outside not in direct_result

    @pytest.mark.parametrize(
        "malicious_css",
        [
            'body { background-image: image-set("file:///etc/passwd" 1x); }',
            'body { background-image: -webkit-image-set(url("file:///etc/passwd") 1x); }',
            'body { background-image: -moz-image-set("https://evil.com/x.png" 1x); }',
            'body { background-image: src("file:///etc/passwd"); }',
            r'body { background-image: \69mage-set("file:///etc/passwd" 1x); }',
            'body { background-image: image-set("https://evil.com/a.png" 1x); color: green; }',
        ],
    )
    def test_blocks_css_image_set_and_src_function_bypasses(
        self, tmp_path: Path, malicious_css: str
    ) -> None:
        result = sanitize_css(malicious_css, str(tmp_path))
        assert "/etc/passwd" not in result
        assert "evil.com" not in result
        assert "image-set" not in result.lower()

    def test_blocks_inline_style_image_set_src_and_escaped_url(self) -> None:
        payload = (
            "<div style=\"background-image: image-set('file:///etc/passwd' 1x); "
            "-webkit-image-set('https://evil.com/a.png' 1x); "
            r"background: \75\72\6c(file:///etc/passwd); "
            'content: src(\'https://evil.com/b.png\'); color: navy;">Hi</div>'
        )
        result = sanitize_html(payload)
        assert "/etc/passwd" not in result
        assert "evil.com" not in result
        assert "image-set" not in result.lower()
        assert 'style="color: navy"' in result

    @pytest.mark.parametrize(
        "src",
        [
            "javascript:alert(1)",
            "  JaVaScRiPt:alert(1)",
            "&#106;avascript:alert(1)",
            "java&#x0a;script:alert(1)",
            "vbscript:msgbox(1)",
            "livescript:alert(1)",
            "blob:https://evil.example/123",
            "filesystem:https://evil.example/a.png",
            "ftp://evil.example/a.png",
            "data:text/html,<script>alert(1)</script>",
            "data&#58;text/html,<script>1</script>",
        ],
    )
    def test_img_src_scheme_allowlist_blocks_non_allowlisted_schemes(self, src: str) -> None:
        assert sanitize_html(f'<img src="{src}" alt="x">') == '<img alt="x" />'

    def test_blocks_svg_xss_event_handlers_and_style_import_vectors(self) -> None:
        payload = (
            '<svg><animate onbegin="alert(1)" /><a xlink:href="javascript:alert(1)">x</a>'
            '<foreignObject><script>alert(1)</script></foreignObject></svg>'
            '<div onpointerenter="alert(1)" onanimationstart="alert(2)">ok</div>'
            '<style>@import "https://evil.com/leak.css";</style>'
            '<style type="text/css">/* </style><script>alert(1)</script> */</style>'
        )
        result = sanitize_html(payload)
        assert "<svg" not in result.lower()
        assert "<script" not in result.lower()
        assert "alert(" not in result.lower()
        assert "onpointerenter" not in result.lower()
        assert "onanimationstart" not in result.lower()
        assert "@import" not in result.lower()
        assert "<div>ok</div>" in result
