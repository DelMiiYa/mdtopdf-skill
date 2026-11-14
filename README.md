# `mdtopdf-skill`

Headless command-line tool (`mdtopdf`) and Antigravity skill (`skills/md-to-pdf/SKILL.md`) for converting Markdown documents (including Thai/Unicode text, tables, and local images) into print-ready A4 PDFs.

## Architecture & Single Source of Truth (SSOT)

`mdtopdf-skill` is a thin CLI and agent-skill wrapper around **`mdtopdf_core`** from the upstream repository [`DelMiiYa/Markdown2PDF`](https://github.com/DelMiiYa/Markdown2PDF).

- **No duplicated conversion engine:** All HTML rendering, CSS styling, path sanitization, local image resolution, and headless browser invocation live exclusively in `mdtopdf_core`.
- **Runtime requirement:** Microsoft Edge, Google Chrome, or Chromium must be installed on the host machine (or specified via the `MDTOPDF_BROWSER` environment variable).

## Installation

```bash
python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\pip install -e ".[dev]"
# Linux / macOS
.venv/bin/pip install -e ".[dev]"
```

## CLI Usage & Flags Reference

```text
mdtopdf INPUT.md [-o OUT.pdf] [--base-dir DIR]
        [--font-size PT] [--line-height N] [--margin {compact,standard,comfortable,wide}|"12mm 14mm"]
        [--img-max-width PX] [--img-max-height PX]
        [--keep-missing-images] [--no-keep-together] [--accent "#hex"] [--title TEXT]
        [--allow-outside-base-dir] [--json] [--quiet] [--version]
```

### Positional & I/O Arguments

| Argument / Flag | Default | Description |
| :--- | :--- | :--- |
| `INPUT.md` | *(required)* | Input Markdown file path, or `-` to read UTF-8 Markdown from `stdin` (UTF-8 BOM tolerated). |
| `-o`, `--output OUT.pdf` | `<INPUT>.pdf` | Output PDF file path (defaults to `<INPUT>.pdf`, or `<base-dir>/stdin.pdf` for `-`; if the default input directory is read-only, falls back to CWD or temp dir and records an `output_fallback` warning). |
| `--base-dir DIR` | Input file's parent directory | Base directory used to resolve relative image paths. **Required** when reading from `stdin` (`-`). |

### Styling & Page Layout Flags

| Flag | Default | Validation / Allowed Values | Description |
| :--- | :--- | :--- | :--- |
| `--font-size PT` | `9.5` | `6.0` – `72.0` (finite float) | Base body font size in points. |
| `--line-height N` | `1.45` | `1.0` – `3.0` (finite float) | Body line-height multiplier. |
| `--margin PRESET_OR_CSS` | `standard` (`"12mm 14mm"`) | `compact`, `standard`, `comfortable`, `wide`, or a CSS margin string (e.g. `"12mm 14mm"`) | Page margin preset (case-insensitive) or raw CSS margin specification. |
| `--img-max-width PX` | `90` | Positive integer (`>= 1`) | Maximum table image width in pixels passed to `ConversionOptions`. |
| `--img-max-height PX` | `70` | Positive integer (`>= 1`) | Maximum table image height in pixels passed to `ConversionOptions`. |
| `--keep-missing-images` | `False` (`suppress_missing_images=True`) | Flag | Do not strip missing local images before rendering. By default, missing images are suppressed and reported as warnings. |
| `--no-keep-together` | `False` (`keep_blocks_together=True`) | Flag | Allow page breaks inside tables, code blocks, and callout blocks. |
| `--accent "#hex"` | `"#0d47a1"` | CSS hex color | Accent color for headings, table headers, and callout borders. |
| `--title TEXT` | First H1 heading, else filename stem (`"stdin"` for `-`) | String | Optional document title metadata. |

### Security & Output Mode Flags

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--allow-outside-base-dir` | `False` | **Warning:** Disables path-traversal containment so local images outside `--base-dir` can be embedded. UNC paths and remote `file://host` URLs remain blocked by `mdtopdf_core`. |
| `--json` | `False` | Emit a single-line JSON result object to `stdout` on both success and error. |
| `--quiet` | `False` | Suppress non-error human-readable status output. |
| `--version` | — | Print `mdtopdf-skill <version> (mdtopdf_core <version>)` and exit. |

### Exit Codes

| Exit Code | Meaning | Notes |
| :--- | :--- | :--- |
| `0` | Success | PDF rendered with ≥ 1 page. Suppressed images and warnings (`literal_math`, `output_fallback`) emit on `stderr` and populate `suppressed_images` / `warnings` in `--json` while still exiting `0`. |
| `2` | Bad arguments or missing input | Invalid flag values (NaN/inf, out-of-range numbers), missing input file, `-` without `--base-dir`, or explicit `-o` targeting a non-writable directory. |
| `3` | No browser found | Edge/Chrome/Chromium not detected. Set `MDTOPDF_BROWSER=/path/to/browser` to override. |
| `4` | Render failure | Headless browser or conversion runtime error (`RuntimeError` from `mdtopdf_core`). |
| `5` | Empty output PDF | Output PDF file is missing, empty (0 bytes), or has `0` pages. |

### JSON Output Schema (`--json`)

- **Success (`exit 0`):**
  ```json
  {"ok": true, "output": "path/to/out.pdf", "pages": 3, "embedded_images": 2, "suppressed_images": [], "warnings": [], "core_version": "1.2.0", "elapsed_seconds": 0.84}
  ```
- **Failure (`exit 2..5`):**
  ```json
  {"ok": false, "error": "Human-readable error message", "code": 3}
  ```

## Known Upstream Limitations

1. **External local images dropped by default:** Absolute `file:///` or relative `../` image paths resolving outside `--base-dir` are suppressed unless `--allow-outside-base-dir` is explicitly passed.
2. **Raw LaTeX math:** Inline `$...$` and display `$$...$$` LaTeX expressions are rendered verbatim as plain text and surfaced in `warnings` (`literal_math`).

## Bumping the Upstream `mdtopdf_core` Pin

Because all conversion logic lives in `DelMiiYa/Markdown2PDF`, bug fixes or rendering enhancements must be released upstream first and then bumped here:

1. Land and test the change in [`DelMiiYa/Markdown2PDF`](https://github.com/DelMiiYa/Markdown2PDF), ensuring `mdtopdf_core` remains GUI-free (`pytest tests/test_core_boundary.py`).
2. Tag and push the new upstream release (for example, `git tag v1.3.0 && git push origin v1.3.0`).
3. Update the git tag in [`pyproject.toml`](pyproject.toml):
   ```toml
   dependencies = [
       "markdown2pdf @ git+https://github.com/DelMiiYa/Markdown2PDF.git@v1.3.0",
   ]
   ```
4. Reinstall in your virtual environment and run the full verification suite:
   ```bash
   pip install -e ".[dev]" --upgrade
   ruff check .
   mypy src tests
   pytest
   ```
5. Commit the updated `pyproject.toml` and open a pull request.
