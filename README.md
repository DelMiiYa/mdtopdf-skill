# `mdtopdf-skill`

Standalone headless CLI (`mdtopdf`) and Antigravity skill (`skills/md-to-pdf/SKILL.md`) for converting Markdown documents—including 7 opinionated print themes, YAML frontmatter, offline Mermaid diagrams, offline KaTeX math, Thai/Unicode typography, tables, and local images—into verified print-ready PDFs via localhost Chrome DevTools Protocol (CDP).

## Architecture

`mdtopdf-skill` v1.0.0 is completely self-contained (`src/mdtopdf_skill/`) with standard PyPI dependencies (`markdown`, `pymupdf`, `pillow`, `pyyaml`, `pygments`) and bundled offline assets (`mermaid@11.17.2` and `katex@0.19.1` with `.woff2` fonts under `src/mdtopdf_skill/assets/`).

- **Zero external runtime services or CDN calls:** Mermaid and KaTeX are lazy-injected from local package assets only when diagrams or math appear in the document, protected by a per-render cryptographic CSP `nonce`.
- **Single-session print-geometry CDP driver (`cdp_browser.py` + `autofit.py`):** Emulates `print` media at exact printable page dimensions, detects/remediates table overflows and trailing orphan pages in-session, captures Mermaid/KaTeX errors, and optionally exports 144 DPI preview PNGs.
- **Browser requirement:** Microsoft Edge, Google Chrome, or Chromium installed on the host machine (or configured via `MDTOPDF_BROWSER`).

## Installation

```bash
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\pip install -e ".[dev]"
# Linux / macOS
.venv/bin/pip install -e ".[dev]"
```

## Print Themes (`--theme`)

All themes include cross-platform Windows + Linux Thai/Unicode font stacks and respect `--spacing-scale`:

| Theme | Intended Use | Font Size | Line Height | Margin | Accent | Page Numbers |
| :--- | :--- | :---: | :---: | :--- | :--- | :---: |
| `office` *(default)* | Corporate memos, business reports, SOPs | `9.5pt` | `1.45` | `15mm 16mm` | `#0f3d7a` | `true` |
| `student` | Study notes, lab reports, homework | `10.0pt` | `1.52` | `14mm 15mm` | `#2b4c7e` | `true` |
| `paper` | Academic & research papers (serif + booktabs) | `10.0pt` | `1.48` | `18mm 20mm` | `#1e293b` | `true` |
| `tech-spec` | Engineering RFCs, architecture & API docs | `9.2pt` | `1.42` | `13mm 14mm` | `#0369a1` | `true` |
| `executive` | Executive briefs, proposals, summaries | `10.2pt` | `1.50` | `16mm 18mm` | `#1d4ed8` | `true` |
| `minimal` | Swiss minimalist docs, CVs, clean notes | `9.5pt` | `1.48` | `16mm 18mm` | `#111827` | `true` |
| `classic` | Backward-compatible v0.2.0 aesthetic | `9.5pt` | `1.45` | `12mm 14mm` | `#0d47a1` | `false` |

## Configuration Precedence & YAML Frontmatter

Options resolve from highest to lowest priority:
1. **Explicit CLI flags**
2. **YAML Frontmatter** (leading `---` block)
3. **Selected Theme Defaults**
4. **Global Fallback Defaults**

Supported YAML frontmatter keys (hyphen aliases supported where noted):
- **Layout & Styling:** `theme`, `lang` (auto-detects `th` vs `en` when omitted), `font_size` (`font-size`), `line_height` (`line-height`), `spacing_scale` (`spacing-scale`), `margin`, `page_size` (`page-size`), `accent` (`accent_color`), `page_numbers` (`page-numbers`), `header` (`str | bool`), `footer` (`str | bool`), `toc` (`bool`), `css` (inline CSS or relative `.css` file inside `--base-dir`).
- **Metadata Header Fields:** `title`, `subtitle`, `author`, `date`, `course`, `instructor`, `student`, `affiliation`, `abstract`, `status`, `version`, `owner`. When frontmatter renders a title/metadata block and the Markdown body begins with a matching `# H1`, the duplicate leading `<h1>` is automatically removed.

CSS rules cascade deterministically: Theme CSS → Frontmatter `css` → CLI `--css` → Auto-fit overrides.

## CLI Usage & Flags Reference

```text
mdtopdf [INPUT.md] [-o OUT.pdf] [--base-dir DIR]
        [--theme {office,student,paper,tech-spec,executive,minimal,classic}]
        [--list-themes]
        [--font-size PT] [--line-height N] [--spacing-scale N]
        [--margin {compact,standard,comfortable,wide}|"12mm 14mm"]
        [--page-size "A4 portrait"]
        [--img-max-width PX] [--img-max-height PX]
        [--accent "#hex"] [--title TEXT]
        [--css PATH] [--toc | --no-toc] [--no-page-numbers]
        [--keep-missing-images] [--no-keep-together]
        [--no-auto-fit] [--strict] [--preview-dir DIR]
        [--allow-outside-base-dir] [--json] [--quiet] [--version]
```

### Flags Summary

| Flag | Description |
| :--- | :--- |
| `INPUT.md` | Input Markdown file path, or `-` to read UTF-8 Markdown from `stdin` (BOM tolerated). Required unless `--list-themes` is passed. |
| `-o`, `--output OUT.pdf` | Output PDF file path (defaults to `<INPUT>.pdf`, or `<base-dir>/stdin.pdf`; falls back to CWD/temp if default input directory is read-only). |
| `--base-dir DIR` | Base directory for resolving local images and CSS files (defaults to input file's directory; required with `-`). |
| `--theme NAME` | Built-in print theme name (case-insensitive). |
| `--list-themes` | Print all 7 themes with descriptions (`--json` supported) and exit `0`. |
| `--font-size PT` | Base body font size (`6.0`–`72.0` pt). |
| `--line-height N` | Body line-height multiplier (`1.0`–`3.0`). |
| `--spacing-scale N` | Uniform vertical spacing multiplier (`0.5`–`2.0`). |
| `--margin SPEC` | Preset (`compact`, `standard`, `comfortable`, `wide`) or 1–4 physical CSS lengths (`mm`, `cm`, `in`, `pt`, `pc`, `px`, `0`). |
| `--page-size SPEC` | Preset (`A4`, `Letter`, `Legal` with optional `portrait`/`landscape`) or `<width> <height>` CSS physical lengths. |
| `--img-max-width PX`, `--img-max-height PX` | Positive integers in CSS pixels constraining images inside table cells (defaults `90` / `70`). |
| `--accent COLOR` | CSS hex (`#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`) or named color. |
| `--title TEXT` | Document title override (falls back to frontmatter `title`, first ATX `# H1`, or filename stem). |
| `--css PATH` | Local CSS file sanitized and appended after frontmatter CSS (must resolve inside `--base-dir` unless `--allow-outside-base-dir` is passed). |
| `--toc` / `--no-toc` | Enable or disable Table of Contents generation. |
| `--no-page-numbers` | Disable page numbers in the running footer. |
| `--keep-missing-images` | Keep `<img>` tags for missing local files instead of suppressing them. |
| `--no-keep-together` | Allow page breaks inside table rows and grouped heading/list blocks. |
| `--no-auto-fit` | Disable automatic table compaction and trailing-orphan-page retries (diagnostics still run). |
| `--strict` | Exit with code `6` if unresolved `layout_issues`, `mermaid_errors`, or `math_errors` remain. |
| `--preview-dir DIR` | Render each page as `page_001.png`, `page_002.png`, ... at 144 DPI via PyMuPDF. |
| `--allow-outside-base-dir` | **Warning:** Disables `--base-dir` containment for local images and CSS files (UNC paths and remote `http(s):` / `file://host` remain blocked). |
| `--json` | Emit a single-line JSON result object on `stdout`. |
| `--quiet` | Suppress non-error human-readable output. |
| `--version` | Print `mdtopdf-skill 1.0.0` and exit `0`. |

### Exit Codes

| Code | Constant | Meaning |
| :---: | :--- | :--- |
| `0` | `EXIT_OK` | Success (`pages >= 1`; check `layout_issues`, `mermaid_errors`, `math_errors`, `warnings`, `suppressed_images` unless `--strict` is set). |
| `2` | `EXIT_BAD_ARGS` | Invalid CLI arguments, missing input, invalid `--css` path, out-of-range/non-finite numbers, `-` without `--base-dir`, output==input collision, or non-writable explicit `-o`/`--preview-dir`. |
| `3` | `EXIT_NO_BROWSER` | No Edge/Chrome/Chromium binary detected (set `MDTOPDF_BROWSER=/path/to/browser`). |
| `4` | `EXIT_RENDER_ERROR` | Headless browser launch, navigation, timeout (`MDTOPDF_TIMEOUT`, default `120`s), or CDP runtime error. |
| `5` | `EXIT_EMPTY_PDF` | Output PDF missing, `0` bytes, or `0` pages. |
| `6` | `EXIT_STRICT_ISSUE` | `--strict` mode failed due to unresolved `layout_issues`, `mermaid_errors`, or `math_errors`. |

### `--json` Output Schemas

- **Conversion Success (`exit 0`):**
  ```json
  {
    "ok": true,
    "output": "path/to/out.pdf",
    "theme": "office",
    "pages": 3,
    "embedded_images": 2,
    "suppressed_images": [],
    "warnings": [],
    "layout_issues": [],
    "mermaid_errors": [],
    "math_errors": [],
    "adjustments_applied": [],
    "last_page_fill_ratio": 0.64,
    "preview_images": [],
    "version": "1.0.0",
    "elapsed_seconds": 0.92
  }
  ```
- **Strict Failure (`exit 6`):** Same diagnostic fields with `"ok": false`, `"code": 6`, and `"error": "Strict mode failed: ..."`.
- **Standard Error (`exit 2..5`):** `{"ok": false, "error": "...", "code": 3}`.
- **`--list-themes --json` (`exit 0`):** `{"ok": true, "themes": [...], "version": "1.0.0"}`.

## Browser Sandbox

The Chromium sandbox is **enabled by default**. If the browser cannot start (for example, running as root in a container, or a CI runner that restricts user namespaces), set `MDTOPDF_NO_SANDBOX=1` to opt out. Only do this for trusted Markdown input.

## Offline Assets & Licenses

Third-party MIT licenses, upstream pinned versions (`mermaid@11.17.2`, `katex@0.19.1`), and SHA-256 checksums for all 23 vendored offline assets are documented in [`src/mdtopdf_skill/assets/THIRD_PARTY_LICENSES.md`](src/mdtopdf_skill/assets/THIRD_PARTY_LICENSES.md).

## Development & Verification

```bash
ruff check .
mypy src tests
bandit -r src/
pytest -v
```
