# QA & Security Code Review Report — `mdtopdf-skill` v1.0.0

**Target Repository:** `e:\Antigravity-Projects\mdtopdf-skill`  
**Audit Scope:** Standalone v1.0.0 decoupled engine, CLI, offline assets, Antigravity skill, tests, and CI (`handoff/IMPLEMENTATION_PLAN.md`)  
**Audit Environment:** Windows (`win32`), Python `3.13.0`, Microsoft Edge (Headless Chromium CDP), PyMuPDF `1.28.2`, Pillow `13.0.0`, Markdown `3.11.3`, PyYAML `6.0.3`, Pygments `2.21.0`, Ruff, Mypy (strict), Bandit  

---

## 1. Executive Summary

### Release Recommendation: **GO (`0` Open Critical / High / Medium / Low Findings)**

`mdtopdf-skill` v1.0.0 cleanly eliminates the external `mdtopdf_core` / `Markdown2PDF` git dependency and delivers a self-contained, PyPI-packaged Markdown-to-PDF engine and Antigravity skill:

- **Full Test Suite:** **662 passed, 0 failed** in `260.23s` across unit, boundary, requirements, CLI execution, and real headless-browser E2E suites (`tests/test_e2e.py` + all 134 opaque-box tests in `tests/test_e2e_tiers.py`).
- **Coverage (`src/mdtopdf_skill`):** **95%** overall (`3,230` statements, `146` missed; `100%` on `__init__.py`, `frontmatter.py`, `sanitizer.py`, and `themes.py`; `98%` on `models.py`; `97%` on `markdown_pipeline.py` and `renderer.py`; `95%` on `autofit.py` and `cli.py`; `85%` on `cdp_browser.py`).
- **Static & Security Checks:** `ruff check .` (`0` errors), `mypy src tests` strict mode (`0` issues across all 24 source files), `bandit -r src/` (`0` High, `0` Medium, `0` Low across 6,581 LOC), `node --check src/mdtopdf_skill/assets/render_bridge.js` (`0` errors).
- **Clean-Venv Packaging:** Wheel build (`mdtopdf_skill-1.0.0-py3-none-any.whl`) and installation in a clean temporary `.venv` outside the repo succeeded with PyPI-only runtime dependencies; `mdtopdf --version` printed `mdtopdf-skill 1.0.0`, `import mdtopdf_core` raised `ModuleNotFoundError`, and all 23 vendored assets + `render_bridge.js` were present in `site-packages`.

---

## 2. Verification Summary

### Static, Security & Asset Integrity Checks

| Check | Command | Result |
| :--- | :--- | :--- |
| **Linter** | `.\.venv\Scripts\ruff.exe check .` | **Passed** (`All checks passed!`) |
| **Type Checker (Strict)** | `.\.venv\Scripts\mypy.exe src tests` | **Passed** (`Success: no issues found in 24 source files`) |
| **Security Scanner** | `.\.venv\Scripts\bandit.exe -r src/` | **Passed** (`0` High, `0` Medium, `0` Low; 2 intentional `# nosec` annotations in `cdp_browser.py` for `subprocess` browser launch) |
| **JS Bridge Syntax** | `node --check src/mdtopdf_skill/assets/render_bridge.js` | **Passed** (`exit 0`) |
| **Vendored Assets SHA-256** | `pytest -v tests/test_e2e_requirements.py -k "asset or sha or license"` | **Passed** (`3/3`: 23/23 SHA-256 checksums verified, `.woff2`-only KaTeX fonts, offline CSS) |
| **Full Test Suite + Cov** | `.\.venv\Scripts\pytest.exe -q --cov=mdtopdf_skill --cov-report=term-missing` | **Passed** (`662 passed` in `260.23s`, `95%` total coverage) |

### Architecture & Security Review Checklist

1. **Decoupling & Boundary Compliance (`tests/test_architecture_boundary.py`):**
   - Zero `mdtopdf_core`, `markdown2pdf`, `tkinter`, `app`, `converter`, or `fitz` imports anywhere in `src/` or `tests/`.
   - Zero CDN URLs (`cdn.jsdelivr.net`, `unpkg.com`, `cdnjs.cloudflare.com`, `fonts.googleapis.com`) in `src/` or `skills/`.
   - `markdown_pipeline.py` has no module-level `themes` import; `renderer.py` resolves theme outputs and injects them.
   - Theme defaults live solely in `themes.py` / `models.py`.
2. **XSS, CSP & Path Containment (`sanitizer.py`, `markdown_pipeline.py`, `cli.py`):**
   - Untrusted HTML is sanitized via allowlist before restoring HTML-escaped Mermaid and KaTeX placeholders.
   - Random per-render `secrets.token_hex(16)` CSP `nonce` enforces `default-src 'none'; img-src file: data:; style-src 'unsafe-inline' file:; font-src file: data:; script-src 'nonce-{nonce}';`.
   - `katex.min.css` is loaded via `<link rel="stylesheet">` so relative `fonts/KaTeX_*.woff2` URLs resolve under `assets/katex/` independently of `<base href>`.
   - Images, frontmatter `.css`, and CLI `--css` enforce strict `base_dir` containment by default; remote `http(s):`, UNC `\\`/`//`, `file://host`, `@import`, `</style>`, `expression()`, and control characters are blocked.
   - Untrusted `suppressed_images` and `warnings` strings are control-char-escaped (`_sanitize_for_stderr`) prior to `stderr` output.
3. **CLI Contract, Precedence & Hybrid Auto-Fit (`models.py`, `frontmatter.py`, `autofit.py`, `cli.py`):**
   - Overridable CLI flags use `default=None` in `argparse`, resolving strictly `CLI > Frontmatter > Theme > Global`.
   - All 7 themes (`office`, `student`, `paper`, `tech-spec`, `executive`, `minimal`, `classic`), YAML frontmatter (25 keys + aliases + H1 deduplication), and hybrid auto-fit (Tier 1 table wrap/compact, Tier 2 proportional scaling floored at `0.65x`, wide non-table element detection, and PyMuPDF body-rect orphan detection with 3 bounded spacing retries) are implemented and verified end-to-end.
   - Exit codes `0`, `2`, `3`, `4`, `5`, and `6` (`--strict`) and all 4 `--json` schemas conform to `handoff/IMPLEMENTATION_PLAN.md` §4.

---

## 3. Fixes Applied During Final Step C E2E Verification

During the full 138-test real-browser E2E pass, 5 failures were identified and resolved in `src/` prior to final acceptance:

1. **`src/mdtopdf_skill/themes.py` (`.mermaid-error-badge` & `.doc-header-status-badge`):** Removed `text-transform: uppercase;` so rendered PDF text preserves `"Mermaid Render Error"` and user frontmatter status casing (e.g., `"Approved"`).
2. **`src/mdtopdf_skill/themes.py` (`.doc-header-meta-grid`):** Widened grid column minimum from `minmax(150px, 1fr)` to `minmax(240px, 1fr)` so `student` theme metadata items (`Course`, `Instructor`, `Student`, `Date`) render as a clean 2-column grid without mid-value line wrapping.
3. **`src/mdtopdf_skill/autofit.py` (`_TABLE_MIN_COL_WIDTH_PX`):** Adjusted Tier 1 table cell minimum width from `62px` to `74px` so 8–11 column tables still remediate cleanly in portrait while 14–18 column tables exceeding the `0.65x` minimum scale floor trigger `table_overflow` (`exit 6` under `--strict`) in portrait and resolve cleanly under `--page-size "A4 landscape"`.

*(Previously resolved in Step B pre-integration: swapped header/footer horizontal CSS padding shorthand in `themes.py` and `.katex-display`/`<pre>` `scrollWidth` width detection in `render_bridge.js`.)*

---

## 4. Scope & Environmental Notes

1. **Full Scope Retained:** All 7 themes, PyMuPDF trailing-orphan detection/remediation, and `wide_element` reporting (originally optional/deferrable in §12 scope tiers) are implemented, unit-tested, and verified end-to-end in v1.0.0.
2. **Platform Verification:** Local verification ran on Windows (`win32`, Python 3.13.0, Microsoft Edge). Ubuntu Linux (`ubuntu-latest`, Python 3.10 & 3.12 with `fonts-noto`, `fonts-noto-color-emoji`, and `fonts-thai-tlwg`) is configured in `.github/workflows/ci.yml` for GitHub Actions CI execution.
3. **Working Tree State:** Changes are verified in the workspace and remain uncommitted pending user commit review.
