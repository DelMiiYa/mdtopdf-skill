"""Unit tests for mdtopdf_skill.markdown_pipeline."""

from __future__ import annotations

import ast
import importlib
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

import mdtopdf_skill.markdown_pipeline as pipeline_mod
from mdtopdf_skill.markdown_pipeline import BuiltDocument, build_html_document
from mdtopdf_skill.models import ConversionOptions, FrontmatterData


def _build(
    md_body: str,
    base_dir: Path,
    *,
    options: ConversionOptions | None = None,
    fm: FrontmatterData | None = None,
    printable_height_mm: float = 267.0,
    theme_css: str | None = "body { color: #111; }",
    metadata_header_html: str | None = "",
    dedup_title: str | None = None,
) -> BuiltDocument:
    """Helper calling build_html_document with stub theme outputs by default."""
    return build_html_document(
        md_body,
        str(base_dir),
        options or ConversionOptions(),
        fm or FrontmatterData(),
        printable_height_mm,
        theme_css=theme_css,
        metadata_header_html=metadata_header_html,
        dedup_title=dedup_title,
    )


class TestArchitectureDecoupling:
    """Verify markdown_pipeline.py has zero module-level dependency on themes.py."""

    def test_no_module_level_themes_import_in_ast(self) -> None:
        src_path = Path(pipeline_mod.__file__)
        tree = ast.parse(src_path.read_text(encoding="utf-8"), filename=str(src_path))
        top_level_imports = [
            node
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom)) and "themes" in ast.dump(node)
        ]
        assert top_level_imports == []

    def test_importing_pipeline_does_not_import_themes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delitem(sys.modules, "mdtopdf_skill.themes", raising=False)
        importlib.reload(pipeline_mod)
        assert "mdtopdf_skill.themes" not in sys.modules

    def test_lazy_themes_import_when_omitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_themes = ModuleType("mdtopdf_skill.themes")
        calls: list[str] = []

        def _fake_theme_css(opts: ConversionOptions, height_mm: float) -> str:
            calls.append(f"css:{opts.theme}:{height_mm}")
            return "body { font-size: 10pt; }"

        def _fake_header(opts: ConversionOptions, fm: FrontmatterData) -> tuple[str, str | None]:
            calls.append(f"hdr:{fm.title}")
            return ('<header class="doc-header"><h1>Doc Title</h1></header>', "Doc Title")

        fake_themes.build_theme_css = _fake_theme_css  # type: ignore[attr-defined]
        fake_themes.build_metadata_header_html = _fake_header  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "mdtopdf_skill.themes", fake_themes)

        doc = build_html_document(
            "# Doc Title\n\nBody text.",
            str(tmp_path),
            ConversionOptions(title="Doc Title"),
            FrontmatterData(title="Doc Title"),
            267.0,
        )
        assert calls == ["hdr:Doc Title", "css:classic:267.0"]
        assert '<header class="doc-header"><h1>Doc Title</h1></header>' in doc.html
        assert "Body text." in doc.html
        assert doc.html.count("Doc Title") == 2  # <title> + <header>, leading H1 deduplicated


class TestMaskingMermaidMathAndCollisions:
    """Verify pre-Markdown masking of Mermaid, KaTeX math, code blocks, and currency."""

    def test_mermaid_fence_and_xss_escaping(self, tmp_path: Path) -> None:
        md = (
            "Intro\n\n"
            "```mermaid\n"
            'graph TD; A["<script>alert(1)</script>"] --> B;\n'
            "```\n\n"
            "~~~mermaid\n"
            "sequenceDiagram\nA->>B: Hi\n"
            "~~~\n"
        )
        doc = _build(md, tmp_path)
        assert doc.has_mermaid is True
        assert doc.has_math is False
        assert '<div class="mermaid-block" data-mermaid-index="0" data-mermaid-src="' in doc.html
        assert '<div class="mermaid-block" data-mermaid-index="1" data-mermaid-src="' in doc.html
        assert "<p><div" not in doc.html
        assert "<script>alert(1)</script>" not in doc.html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in doc.html

    def test_unclosed_fence_and_nested_markdown_fence_not_mermaid(self, tmp_path: Path) -> None:
        md = (
            "````markdown\n"
            "```mermaid\n"
            "graph TD; A-->B;\n"
            "```\n"
            "````\n\n"
            "```mermaid\n"
            "unclosed diagram\n"
        )
        doc = _build(md, tmp_path)
        assert doc.has_mermaid is False
        assert "mermaid-block" not in doc.html
        assert "mermaid.min.js" not in doc.html

    def test_inline_and_display_math_protection_and_currency_rules(self, tmp_path: Path) -> None:
        md = (
            r"Escaped \$x\$ stays literal." + "\n\n"
            "Costs $5 and $10 or $1,234.50 and $5 - $10 range.\n\n"
            "Budget $500, where $x^2 + y_1 < z$ is tax, and $1 + 2 = 3$.\n\n"
            "Link [price $x$](https://example.com/$1/$2) and ![alt $5](missing.png) "
            'and <a href="https://example.com/$a">tag</a> and `$not_math$`.\n\n'
            "| Col A | Col B |\n"
            "| :--- | ---: |\n"
            "| $a_1$ | $100 |\n\n"
            "$$\n"
            "\\begin{aligned}\n"
            "a_1 &< b_2\n"
            "\\end{aligned}\n"
            "$$\n\n"
            "> [!NOTE]\n"
            "> $$E = mc^2$$\n"
        )
        doc = _build(md, tmp_path)
        assert doc.has_math is True
        assert '<span class="math-inline" data-math-index=' in doc.html
        assert '<div class="math-display" data-math-index=' in doc.html
        assert "x^2 + y_1 &lt; z" in doc.html
        assert "1 + 2 = 3" in doc.html
        assert "a_1 &amp;&lt; b_2" in doc.html
        assert "E = mc^2" in doc.html
        # Currency & escaped/code/URL dollars remain unmasked
        assert "Escaped $x$ stays literal." in doc.html
        assert "Costs $5 and $10 or $1,234.50" in doc.html
        assert "Budget $500," in doc.html
        assert 'href="https://example.com/$1/$2"' in doc.html
        assert 'href="https://example.com/$a"' in doc.html
        assert "<code>$not_math$</code>" in doc.html
        assert "$100" in doc.html
        assert "<p><div class=\"math-display\"" not in doc.html

    def test_placeholder_collision_safety(self, tmp_path: Path) -> None:
        md = (
            "Literal tokens: MDTOPDFMERMAID0END MDTOPDFMATH0END MDTOPDFCODE0END\n\n"
            "```mermaid\ngraph LR; X-->Y;\n```\n\n"
            "Real math $a + b$ and `inline`.\n"
        )
        doc = _build(md, tmp_path)
        assert "MDTOPDFMERMAID0END" in doc.html
        assert "MDTOPDFMATH0END" in doc.html
        assert "MDTOPDFCODE0END" in doc.html
        assert 'class="mermaid-block"' in doc.html
        assert 'class="math-inline"' in doc.html
        assert "<code>inline</code>" in doc.html

    def test_tight_pure_number_inline_math_vs_currency_prose(self, tmp_path: Path) -> None:
        md = (
            "Measured $20.17$ and integer $42$ and ($20.17$) and $42$!\n\n"
            "Costs $5 and $10 or $1,234.50, $5 - $10, $5-$10, and $500. Next $x$ plus $10 20$.\n"
        )
        doc = _build(md, tmp_path)
        assert doc.has_math is True
        assert doc.warnings == []
        assert doc.html.count('class="math-inline"') == 5
        assert ">20.17</span>" in doc.html
        assert ">42</span>" in doc.html
        assert ">x</span>" in doc.html
        assert "Costs $5 and $10 or $1,234.50, $5 - $10, $5-$10, and $500." in doc.html
        assert "$10 20$" in doc.html


class TestMarkdownExtensionsAndTransforms:
    """Verify extensions, GH alerts, task lists, H1 dedup, TOC, and station-block."""

    def test_tables_codehilite_footnotes_and_sane_lists(self, tmp_path: Path) -> None:
        md = (
            "| Left | Center | Right |\n"
            "| :--- | :---: | ---: |\n"
            "| a | b | c |\n\n"
            "```python\ndef hi():\n    return 1\n```\n\n"
            "Text with note[^1].\n\n"
            "[^1]: Footnote body.\n"
        )
        doc = _build(md, tmp_path)
        assert '<th style="text-align:left">Left</th>' in doc.html
        assert '<th style="text-align:center">Center</th>' in doc.html
        assert '<th style="text-align:right">Right</th>' in doc.html
        assert '<div class="highlight">' in doc.html
        assert 'class="footnote-ref"' in doc.html
        assert 'id="fn:1"' in doc.html

    def test_commonmark_nested_lists_tight_lists_and_display_math_in_lists(
        self, tmp_path: Path
    ) -> None:
        md = (
            "Intro paragraph right before tight numbered list:\n"
            "1. First ordered item\n"
            "  - Two-space nested bullet\n"
            "    - Grandchild bullet\n"
            "2. Tenth-style test\n\n"
            "10. Two-digit item\n"
            "  - Two-space child under 10.\n"
            "   - Three-space child under 10.\n"
            "    - Four-space child under 10.\n\n"
            "Tight bullet intro:\n"
            "- Alpha\n"
            "  - Nested alpha\n"
            "- Beta\n\n"
            "1. Item before math\n"
            "  $$E = mc^2$$\n"
            "  - Nested bullet after display math\n"
            "2. Second item after math\n\n"
            "1. Multi-line math item\n\n"
            "   $$\n"
            "   a^2 + b^2 = c^2\n"
            "   $$\n\n"
            "   - Child after multi-line math\n"
            "2. Continues single ordered list\n\n"
            "```markdown\n"
            "1. Code fence stays verbatim\n"
            "  - Not normalized\n"
            "```\n\n"
            "```\n"
            "1. Plain fence\n"
            "  - Keep two spaces\n"
            "```\n"
        )
        doc = _build(md, tmp_path)
        assert doc.has_math is True
        # Tight lists after paragraphs render as <ol> / <ul> instead of prose
        assert "<p>Intro paragraph right before tight numbered list:</p>\n<ol>" in doc.html
        assert "<p>Tight bullet intro:</p>\n<ul>" in doc.html
        # Two-space nested bullets under 1. and 10. stay nested inside parent <li>
        assert (
            "<p>First ordered item</p>\n<ul>\n<li>Two-space nested bullet\n"
            "<ul>\n<li>Grandchild bullet</li>"
        ) in doc.html
        assert (
            "<p>Two-digit item</p>\n<ul>\n<li>Two-space child under 10.\n"
            "<ul>\n<li>Three-space child under 10."
        ) in doc.html
        # Display math inside list items preserves list continuity into 2.
        assert '<div class="math-display" data-math-index="0">E = mc^2</div>' in doc.html
        assert '<div class="math-display" data-math-index="1">a^2 + b^2 = c^2</div>' in doc.html
        assert "<li>\n<p>Second item after math</p>\n</li>" in doc.html
        assert "<li>\n<p>Continues single ordered list</p>\n</li>" in doc.html
        # Fenced code blocks were not mutated by list normalizer (2 spaces kept verbatim)
        assert '<span class="w">  </span><span class="k">-</span>' in doc.html
        assert "<pre><code>1. Plain fence\n  - Keep two spaces\n</code></pre>" in doc.html

    def test_strikethrough_dedup_heading_slugs_and_multiline_footnotes(
        self, tmp_path: Path
    ) -> None:
        md = (
            "# Overview\n\n"
            "## Repeat\n\n"
            "## Repeat\n\n"
            "Deleted ~~old text~~ and inline code `[^1]` ignored.\n\n"
            "Paragraph with note[^1] and second note[^alpha].\n\n"
            "[^1]: First line.\n\n"
            "    Second paragraph in footnote.\n\n"
            "[^alpha]: Second footnote.\n"
        )
        doc = _build(md, tmp_path, options=ConversionOptions(toc=True))
        assert "<s>old text</s>" in doc.html
        assert '<h2 id="repeat">Repeat</h2>' in doc.html
        assert '<h2 id="repeat-1">Repeat</h2>' in doc.html
        assert "<code>[^1]</code> ignored" in doc.html
        assert doc.html.count('class="footnote-ref"') == 2
        assert "Second paragraph in footnote." in doc.html
        assert 'href="#repeat-1"' in doc.html

    def test_github_alerts_all_five_and_consecutive_separation(self, tmp_path: Path) -> None:
        md = (
            "> [!NOTE]\n"
            "> Note body <script>alert(1)</script>\n\n"
            "> [!TIP]\n"
            "> Tip body\n\n"
            "> [!IMPORTANT]\n"
            ">\n"
            "> Multi para important\n\n"
            "> [!WARNING]\n"
            "> Warning body\n\n"
            "> [!CAUTION]\n"
            "> Caution body\n"
        )
        doc = _build(md, tmp_path)
        for kind in ("note", "tip", "important", "warning", "caution"):
            assert f'class="callout callout-{kind}"' in doc.html
            assert f'<p class="callout-title">{kind.title()}</p>' in doc.html
        assert "alert(1)" not in doc.html
        assert "<script>alert(1)" not in doc.html
        assert "Multi para important" in doc.html

    def test_task_lists_and_raw_input_stripping(self, tmp_path: Path) -> None:
        md = (
            "- [ ] Open task\n"
            "- [x] Done lower\n"
            "- [X] Done upper\n"
            "    - [x] Nested task\n\n"
            'Untrusted: <input type="password" value="secret">\n'
        )
        doc = _build(md, tmp_path)
        assert '<li class="task-item"><input type="checkbox" disabled> Open task' in doc.html
        assert doc.html.count('<input type="checkbox" disabled checked>') == 3
        assert "password" not in doc.html

    def test_leading_h1_deduplication_and_toc(self, tmp_path: Path) -> None:
        md = "# **Project** *Report* [v1](https://ex.com)\n\n## Section One\n\nBody\n"
        doc_dedup = _build(
            md,
            tmp_path,
            options=ConversionOptions(toc=True),
            metadata_header_html='<header class="doc-header"><h1>Project Report v1</h1></header>',
            dedup_title="Project Report v1",
        )
        assert '<div class="toc">' in doc_dedup.html
        assert "Section One" in doc_dedup.html
        assert "project-report-v1" not in doc_dedup.html

        # Without metadata header, H1 is preserved
        doc_keep = _build(
            md,
            tmp_path,
            options=ConversionOptions(toc=False),
            metadata_header_html="",
            dedup_title="Project Report v1",
        )
        assert "<h1" in doc_keep.html

        # Literal [TOC] is NOT expanded when options.toc=False
        doc_no_toc = _build("[TOC]\n\n# Heading\n", tmp_path, options=ConversionOptions(toc=False))
        assert "<p>[TOC]</p>" in doc_no_toc.html
        assert '<div class="toc">' not in doc_no_toc.html

        # Inline [TOC] with options.toc=True renders once (not duplicated)
        doc_inline_toc = _build(
            "[TOC]\n\n# Heading\n", tmp_path, options=ConversionOptions(toc=True)
        )
        assert doc_inline_toc.html.count('<div class="toc">') == 1

        # Empty headings with options.toc=True does not emit empty toc div
        doc_empty_toc = _build("Just text.\n", tmp_path, options=ConversionOptions(toc=True))
        assert '<div class="toc">' not in doc_empty_toc.html

    def test_no_legacy_station_block_wrapper(self, tmp_path: Path) -> None:
        md = "### Station 1\n\n- Step 1\n    - Substep 1a\n- Step 2\n"
        doc_wrap = _build(md, tmp_path, options=ConversionOptions(keep_blocks_together=True))
        assert "station-block" not in doc_wrap.html
        assert "Substep 1a" in doc_wrap.html

        doc_nowrap = _build(md, tmp_path, options=ConversionOptions(keep_blocks_together=False))
        assert "station-block" not in doc_nowrap.html


class TestImagesCspCssAndLang:
    """Verify image resolution, HTML XSS, CSS cascade/warnings, CSP nonce, assets, and lang."""

    def test_markdown_and_html_image_containment_and_missing_options(self, tmp_path: Path) -> None:
        local_img = tmp_path / "ok.png"
        local_img.write_bytes(b"PNG")
        outside_img = tmp_path.parent / "outside.png"
        outside_img.write_bytes(b"PNG")

        md = (
            "![ok](ok.png)\n"
            r"![unc](\\server\share\x.png)" + "\n"
            "![remote](https://evil.com/x.png)\n"
            "![outside](../outside.png)\n"
            "![missing](missing.png)\n"
            '<img src="ok.png" alt="html-ok">\n'
            '<img src="https://evil.com/tracker.png" alt="bad">\n'
            '<img src="javascript:alert(1)" alt="xss">\n'
            '<img src="data:image/png;base64,iVBORw0KGgo=" alt="data-ok">\n'
        )
        doc = _build(md, tmp_path)
        assert doc.html.count(local_img.resolve().as_uri()) == 2
        assert "data:image/png;base64,iVBORw0KGgo=" in doc.html
        assert "evil.com" not in doc.html
        assert r"\\server\share\x.png" in doc.suppressed_images
        assert "https://evil.com/x.png" in doc.suppressed_images
        assert "../outside.png" in doc.suppressed_images
        assert "missing.png" in doc.suppressed_images
        assert "https://evil.com/tracker.png" in doc.suppressed_images
        assert "[unsafe-src]" in doc.suppressed_images

        # --keep-missing-images and --allow-outside-base-dir
        doc_keep = _build(
            "![outside](../outside.png)\n![missing](missing.png)\n![unc](//host/s/x.png)\n",
            tmp_path,
            options=ConversionOptions(
                suppress_missing_images=False,
                allow_outside_base_dir=True,
            ),
        )
        assert outside_img.resolve().as_uri() in doc_keep.html
        assert (tmp_path / "missing.png").resolve().as_uri() in doc_keep.html
        assert doc_keep.suppressed_images == ["//host/s/x.png"]

    def test_frontmatter_css_file_valid_missing_blocked_and_cascade(self, tmp_path: Path) -> None:
        styles_dir = tmp_path / "styles"
        styles_dir.mkdir()
        custom_css = styles_dir / "custom.css"
        custom_css.write_text("h2 { color: green; } @import 'evil.css';", encoding="utf-8")

        doc_ok = _build(
            "## Hi",
            tmp_path,
            options=ConversionOptions(
                frontmatter_css="styles/custom.css",
                cli_css="h2 { color: navy; }</style><script>1</script>",
            ),
            fm=FrontmatterData(css="styles/custom.css"),
        )
        assert doc_ok.warnings == []
        assert "color: green;" in doc_ok.html
        assert "color: navy;" in doc_ok.html
        assert "@import" not in doc_ok.html
        assert doc_ok.html.index('id="mdtopdf-theme-css"') < doc_ok.html.index(
            'id="mdtopdf-frontmatter-css"'
        )
        assert doc_ok.html.index('id="mdtopdf-frontmatter-css"') < doc_ok.html.index(
            'id="mdtopdf-cli-css"'
        )

        doc_missing = _build(
            "## Hi",
            tmp_path,
            options=ConversionOptions(frontmatter_css="styles/nope.css"),
        )
        assert any(w.startswith("frontmatter_css_missing:") for w in doc_missing.warnings)

        doc_blocked = _build(
            "## Hi",
            tmp_path,
            options=ConversionOptions(frontmatter_css="../secret.css"),
        )
        assert any(w.startswith("frontmatter_css_blocked:") for w in doc_blocked.warnings)

        # Inline frontmatter CSS works directly
        doc_inline = _build(
            "## Hi",
            tmp_path,
            options=ConversionOptions(frontmatter_css="p { margin: 2px; }"),
        )
        assert "p { margin: 2px; }" in doc_inline.html

    def test_csp_nonce_lazy_assets_and_lang_autodetect(self, tmp_path: Path) -> None:
        doc_plain = _build("English paragraph only.", tmp_path)
        assert '<html lang="en">' in doc_plain.html
        assert re.fullmatch(r"[0-9a-f]{32}", doc_plain.nonce)
        expected_csp = (
            "default-src 'none'; img-src file: data:; "
            "style-src 'unsafe-inline' file:; font-src file: data:; "
            f"script-src 'nonce-{doc_plain.nonce}';"
        )
        assert f'content="{expected_csp}"' in doc_plain.html
        assert "mermaid.min.js" not in doc_plain.html
        assert "katex.min.css" not in doc_plain.html
        assert "katex.min.js" not in doc_plain.html
        assert f'<script nonce="{doc_plain.nonce}" src="' in doc_plain.html
        assert "render_bridge.js" in doc_plain.html
        assert "https://" not in doc_plain.html
        assert "http://" not in doc_plain.html

        doc_thai = _build("สวัสดีครับ\n\n```mermaid\ngraph TD; A-->B;\n```\n\n$x^2$\n", tmp_path)
        assert '<html lang="th">' in doc_thai.html
        assert doc_thai.has_mermaid is True
        assert doc_thai.has_math is True
        assert doc_thai.nonce != doc_plain.nonce
        assert "mermaid.min.js" in doc_thai.html
        assert '<link rel="stylesheet" href="' in doc_thai.html
        assert "katex.min.css" in doc_thai.html
        assert "katex.min.js" in doc_thai.html

        doc_fm_lang = _build(
            "สวัสดี",
            tmp_path,
            options=ConversionOptions(lang="en"),
            fm=FrontmatterData(lang="en"),
        )
        assert '<html lang="en">' in doc_fm_lang.html

        doc_opt_lang = _build("English", tmp_path, options=ConversionOptions(lang="ja"))
        assert '<html lang="ja">' in doc_opt_lang.html


class TestEdgeCasesAndCoverage:
    """Cover subtle Markdown parsing, blockquote, CSS file, and lazy-theme edge cases."""

    def test_backtick_info_string_inline_code_and_link_edge_cases(self, tmp_path: Path) -> None:
        img = tmp_path / "foo(1).png"
        img.write_bytes(b"PNG")
        md = (
            "```py`thon\nnot a fence\n```\n\n"
            "``code\nwith newline`` and ``code\n\nblank`` and `unclosed\n\n"
            "``two ``` three``\n\n"
            r"![alt \[1\] [nested]](foo(1).png 'title \'q\'')" + "\n\n"
            "![angle](<foo(1).png> \"t\")\n\n"
            "![newline](\nfoo(1).png)\n\n"
            "![data](data:image/png;base64,iVBORw0KGgo=)\n\n"
            "![broken](\n![unclosed_br\n\n"
            "![bad_nl](foo\nbar)\n\n"
            "![bad_angle](<foo\nbar>)\n\n"
            "![bad_title](foo(1).png \"unclosed)\n\n"
            "![esc_dest](foo\\(1\\).png (paren title))\n"
        )
        doc = _build(md, tmp_path)
        assert "not a fence" in doc.html
        assert "data:image/png;base64,iVBORw0KGgo=" in doc.html
        assert img.resolve().as_uri() in doc.html

    def test_math_edge_cases_and_h1_dedup_edges(self, tmp_path: Path) -> None:
        md = (
            "# Unrelated H1\n\n"
            "Inline $a +\nb$ and $unclosed\n\nblank$ and $x $$y$$.\n\n"
            "Just text $100.\n"
        )
        doc = _build(
            md,
            tmp_path,
            metadata_header_html='<header class="doc-header"><h1>Header</h1></header>',
            dedup_title="   ",
        )
        assert "<h1" in doc.html
        assert "a +\nb" in doc.html

        doc_diff = _build(
            "# Different Heading\n\nBody",
            tmp_path,
            metadata_header_html='<header class="doc-header"><h1>Other</h1></header>',
            dedup_title="Other",
        )
        assert "Different Heading" in doc_diff.html

        doc_no_h1 = _build(
            "Paragraph first.\n",
            tmp_path,
            metadata_header_html='<header class="doc-header"><h1>Other</h1></header>',
            dedup_title="Other",
        )
        assert "Paragraph first." in doc_no_h1.html

    def test_html_img_keep_missing_nested_blockquotes_and_frontmatter_css_variants(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        css_file = tmp_path / "good.css"
        css_file.write_text("body { color: teal; }", encoding="utf-8")

        md = (
            '<img src="missing.png" alt="keep">\n\n'
            "> Outer quote\n>\n> > Nested quote\n\n"
            "<ul><li>keep</li></ul>\n"
        )
        doc = _build(
            md,
            tmp_path,
            options=ConversionOptions(
                suppress_missing_images=False,
                frontmatter_css=css_file.as_uri(),
            ),
        )
        assert (tmp_path / "missing.png").resolve().as_uri() in doc.html
        assert "<blockquote>" in doc.html
        assert "color: teal;" in doc.html

        for bad_css in (
            "//evil/share/a.css",
            r"\\evil\share\a.css",
            "https://evil.com/a.css",
            "file://evilhost/a.css",
            "file:///C:/a%00.css",
            "C:rel.css",
            "bad\x07.css",
        ):
            doc_bad = _build(
                "Hi",
                tmp_path,
                options=ConversionOptions(frontmatter_css=bad_css),
            )
            assert any(w.startswith("frontmatter_css_blocked:") for w in doc_bad.warnings)

        # Unreadable CSS file triggers frontmatter_css_missing
        bad_utf8 = tmp_path / "bad_enc.css"
        bad_utf8.write_bytes(b"\xff\xfe\x00\x80")
        doc_enc = _build("Hi", tmp_path, options=ConversionOptions(frontmatter_css="bad_enc.css"))
        assert any(w.startswith("frontmatter_css_missing:") for w in doc_enc.warnings)

        # Direct helper edge cases: unclosed tags, existing class, lazy css only
        assert "<blockquote" in pipeline_mod._transform_github_alerts("<blockquote>open")
        task_html = pipeline_mod._transform_task_lists('<li class="x">[ ] todo</li>')
        assert 'class="x task-item"' in task_html

        fake_themes = ModuleType("mdtopdf_skill.themes")
        fake_themes.build_theme_css = lambda opts, h: "p { color: blue; }"  # type: ignore[attr-defined]
        fake_themes.build_metadata_header_html = lambda opts, fm: ("", None)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "mdtopdf_skill.themes", fake_themes)
        doc_lazy_css = build_html_document(
            "Hi",
            str(tmp_path),
            ConversionOptions(),
            FrontmatterData(),
            267.0,
            theme_css=None,
            metadata_header_html="<header>H</header>",
            dedup_title="H",
        )
        assert "p { color: blue; }" in doc_lazy_css.html

