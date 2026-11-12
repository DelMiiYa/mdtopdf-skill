# Project Status Log (`mdtopdf-skill`)

Each role agent (§8 of `docs/HANDOFF.md`) appends its status and handoff notes here.

---

## Pre-8.1 Initial Setup (Partially Completed)
**Date:** 2026-11-12

### Already Completed
- Initialized Git repository on branch `main` (`git init -b main`).
- Moved `HANDOFF_CLI_SKILL.md` to `docs/HANDOFF.md`.
- Verified upstream tag `v1.2.0` exists on `origin` (`https://github.com/DelMiiYa/Markdown2PDF.git@v1.2.0`) and contains `mdtopdf_core`.
- Created `.gitignore`.
- Created `pyproject.toml` (`name = "mdtopdf-skill"`, pinned `markdown2pdf @ git+https://github.com/DelMiiYa/Markdown2PDF.git@v1.2.0`, console script `mdtopdf = "mdtopdf_skill.cli:main"`, pytest/mypy/ruff config).
- Created `plugin.json` at repo root (Antigravity plugin manifest for `md-to-pdf`, verified no name collision in installed plugins).

---

## §8.1 Lead / Scaffold Agent – Completed
**Date:** 2026-11-12

### Completed in §8.1
- Reviewed and verified `.gitignore`, `pyproject.toml`, and `plugin.json`.
- Created package skeleton:
  - `src/mdtopdf_skill/__init__.py` (`__version__ = "0.1.0"`)
  - `src/mdtopdf_skill/cli.py` (typed stub `main(argv: list[str] | None = None) -> int`)
  - `skills/md-to-pdf/SKILL.md` (placeholder stub with frontmatter `name: md-to-pdf` and `description`)
  - `tests/__init__.py` and `tests/test_smoke.py` (verifies `mdtopdf_skill` and `mdtopdf_core` imports)
- Created `.github/workflows/ci.yml` running `ruff check .`, `mypy src tests`, and `pytest -v` on `windows-latest` and `ubuntu-latest` (Python 3.10 and 3.12).
- Created `README.md` with architecture/SSOT rules, installation instructions, full CLI flags & exit codes reference (§5), JSON output schema, known upstream limitations (§6), and the `mdtopdf_core` git tag-pin bump procedure (§9.4).
- Created `.venv`, installed `-e ".[dev]"`, verified `python -c "import mdtopdf_core"`, and confirmed `pytest`, `ruff check .`, and `mypy src tests` pass cleanly.
- Committed initial scaffold to `main`.

### Open Tasks for §8.2 Dev Agent (from §5 CLI Contract & §7 SKILL.md Requirements)

#### 1. CLI Implementation (`src/mdtopdf_skill/cli.py` per §5)
- [x] **SSOT Compliance (§2–§3):** Import only from `mdtopdf_core` (`render_pdf`, `ConversionOptions`, `ConversionResult`, `MARGIN_PRESETS`, `find_browser_executable`). Never import `app` or `converter`, and never duplicate conversion/CSS/sanitization logic.
- [x] **Testable Structure (§8.2):** Keep `main(argv: list[str] | None = None) -> int` thin with separate argument-parsing and execution helper functions.
- [x] **Flags & Defaults (§5):**
  - `INPUT.md` positional argument (path or `-` for `stdin`).
  - `-o` / `--output OUT.pdf`: defaults to input path with `.pdf` suffix (handle `stdin` case appropriately).
  - `--base-dir DIR`: defaults to input file's parent directory; **required** when `INPUT.md` is `-` (exit `2` if missing).
  - `--font-size PT`: finite float in `[6.0, 72.0]` (default `9.5`); reject `NaN`, `inf`, or out-of-range values with exit `2`.
  - `--line-height N`: finite float in `[1.0, 3.0]` (default `1.45`); reject `NaN`, `inf`, or out-of-range values with exit `2`.
  - `--margin`: case-insensitive preset (`compact`, `standard`, `comfortable`, `wide` mapped via `MARGIN_PRESETS`) or raw CSS margin string (default `"12mm 14mm"`).
  - `--img-max-width PX` (default `90`) and `--img-max-height PX` (default `70`): validate finite positive numbers before passing to `ConversionOptions`.
  - `--keep-missing-images`: sets `suppress_missing_images=False` (default `True`).
  - `--no-keep-together`: sets `keep_blocks_together=False` (default `True`).
  - `--accent "#hex"`: sets `accent_color` (default `"#0d47a1"`).
  - `--title TEXT`: optional document title passed to `ConversionOptions`.
  - `--allow-outside-base-dir`: off by default (`False`); help text must explicitly warn about disabling path-traversal protection.
  - `--json`: emit single JSON object to `stdout` on success (`{"ok":true,"output":...,"pages":...,"embedded_images":...,"suppressed_images":[...],"elapsed_seconds":...}`) and on error (`{"ok":false,"error":...,"code":...}`).
  - `--quiet`: suppress non-error human-readable output.
  - `--version`: print version and exit `0`.
- [x] **Encoding & Windows I/O (§5):** Read Markdown input as `utf-8-sig` (UTF-8 with BOM tolerated). Ensure `stdout`/`stderr` are UTF-8 safe on Windows (e.g. `reconfigure(encoding="utf-8", errors="replace")` where applicable).
- [x] **Exit Codes (§5):**
  - `0`: Success (PDF exists, size > 0, `page_count >= 1`).
  - `2`: Bad CLI arguments, out-of-range/NaN/inf numbers, missing input file, or `-` without `--base-dir`.
  - `3`: No browser found (`FileNotFoundError` from core / `find_browser_executable`); error message **must mention `MDTOPDF_BROWSER`**.
  - `4`: Render failure (`RuntimeError` or unexpected conversion exception from core).
  - `5`: Output PDF missing, empty (`0` bytes), or `page_count == 0`.
- [x] **Suppressed Images Warning (§5–§6):** When `result.suppressed_images` is non-empty, emit a clear warning on `stderr` (and include the list in `--json` output) while still returning exit code `0`.

#### 2. Antigravity Skill Documentation (`skills/md-to-pdf/SKILL.md` per §7)
- [x] Keep frontmatter `name: md-to-pdf` and `description` specifying when to use (convert/export/print Markdown to PDF, Thai/Unicode docs, local images) and when not to use (editing PDFs, DOCX, LaTeX math).
- [x] Document concise agent workflow:
  1. Check CLI availability (`mdtopdf --version`).
  2. Run conversion with `--json`.
  3. Inspect `suppressed_images` and `pages` in JSON output.
  4. Report output path (and any suppressed-image warnings) to the user.
- [x] Add troubleshooting table keyed by exit codes (`0`, `2`, `3`, `4`, `5`).
- [x] Include 2–3 copy-paste CLI examples.
- [x] Document known upstream limitations (§6): (A) local images outside `base_dir` dropped unless `--allow-outside-base-dir` is set; (B) LaTeX `$...$` printed raw.

---

## §8.2 Dev Agent – Completed
**Date:** 2026-11-12

### Completed in §8.2
- Implemented `src/mdtopdf_skill/cli.py` strictly following the SSOT rule (§2):
  - Imports only `MARGIN_PRESETS`, `ConversionOptions`, `ConversionResult`, `find_browser_executable`, and `render_pdf` from `mdtopdf_core`.
  - Exposes modular, testable functions: `build_parser()`, `parse_args(argv)`, `build_conversion_options(args)`, `resolve_margin_css(raw_margin)`, `run(args)`, and `main(argv)`.
  - Implements all §5 flags (`INPUT.md`, `-o`/`--output`, `--base-dir`, `--font-size`, `--line-height`, `--margin`, `--img-max-width`, `--img-max-height`, `--keep-missing-images`, `--no-keep-together`, `--accent`, `--title`, `--allow-outside-base-dir`, `--json`, `--quiet`, `--version`).
  - Validates numeric inputs before calling core (`--font-size` in `[6, 72]`, `--line-height` in `[1, 3]`, `--img-max-width` / `--img-max-height` `>= 1`, rejecting `NaN`, `inf`, and `-inf`).
  - Reads UTF-8 with BOM tolerated (`utf-8-sig`) for both file paths and `stdin` (`-`), and reconfigures Windows `stdout`/`stderr` to UTF-8.
  - Emits warnings on `stderr` when `suppressed_images` is non-empty while preserving exit code `0`.
  - Implements exact exit codes (`0`, `2`, `3` mentioning `MDTOPDF_BROWSER`, `4`, `5`) and `--json` output on both success and failure.
- Completed `skills/md-to-pdf/SKILL.md` per §7 with frontmatter, 4-step `--json` workflow, prompt-injection guardrail note, 3 copy-paste CLI examples, exit-code troubleshooting table, and §6 upstream limitations.
- Verified `plugin.json` and `README.md` match the CLI contract and Antigravity plugin specification.
- Verified `ruff check .`, `mypy src tests`, and `pytest -v` pass cleanly in `.venv`.
- Verified all flags and exit codes (`0`, `2`, `3`, `4`, `5`) end-to-end on a sample Thai UTF-8 BOM + local PNG image + table `.md` document.
- **Unfinished items / blockers:** None.

### Next Up: §8.3 Test Agent & §8.4 Review Agent
- [x] **§8.3 Test Agent:** Expand `tests/` to cover all CLI flags, validation edge cases (`NaN`/`inf`), exit codes (`0`, `2`, `3`, `4`, `5`), `--json` schema, `stdin`, UTF-8 BOM, Windows paths with spaces/Unicode, `@pytest.mark.e2e` browser tests, and SSOT guard test (no copied `def render_pdf` / `def build_css`).
- [ ] **§8.4 Review Agent:** Adversarial review across SSOT, §5 contract, path-traversal/security, cross-platform behavior, `SKILL.md` prompt-injection/trigger quality, packaging, and CI; record findings in `REVIEW.md`.

---

## §8.3 Test Agent – Completed
**Date:** 2026-11-12

### Completed in §8.3
- Authored comprehensive test suite in `tests/` without modifying `src/mdtopdf_skill/cli.py`:
  - `tests/test_cli_args.py`:
    - Full CLI flag parsing & default options mapping.
    - Numerical range validation: `--font-size` in `[6.0, 72.0]` and `--line-height` in `[1.0, 3.0]`, testing boundary and out-of-boundary values.
    - Non-finite float rejection: `nan`, `inf`, `-inf`, `Infinity` (case-insensitive) rejected with exit code 2.
    - Image dimension validation: `--img-max-width` and `--img-max-height` (positive ints >= 1, non-finite/float/negative rejection).
    - Case-insensitive margin presets (`compact`, `standard`, `comfortable`, `wide` matching upstream `MARGIN_PRESETS`) and raw CSS margin validation.
    - Accent color validation (hex `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, valid color names, invalid syntax rejection).
    - Boolean and string flags: `--keep-missing-images`, `--no-keep-together`, `--allow-outside-base-dir`, `--json`, `--quiet`, `--title`.
    - Stdin `-` requires `--base-dir` validation.
    - `--version` and `--help` exit 0 behavior.
  - `tests/test_cli_execution.py`:
    - Full coverage of all exit codes: `0` (success & quiet mode), `2` (bad args, missing input, stdin without base-dir, non-dir base-dir), `3` (no browser via `find_browser_executable` and `render_pdf`, asserting `MDTOPDF_BROWSER` message), `4` (render failures / `RuntimeError`), `5` (empty PDF: 0 pages or 0 bytes).
    - Complete JSON schema validation on success (`ok: true`, `output`, `pages`, `embedded_images`, `suppressed_images`, `elapsed_seconds`) and on every exit code (`ok: false`, `error`, `code`).
    - Stdin mode (`-` with default and custom `-o`, with UTF-8 BOM).
    - Default output (`.pdf` adjacent to input) and base-dir resolution.
    - Suppressed-image warning behavior on `stderr` preserving exit code `0` (human and JSON mode).
    - UTF-8 with BOM file decoding and international Unicode character support.
    - Windows paths with spaces and Unicode in input, output, and base-dir.
    - I/O error handling, stdio reconfiguration, and module execution via `runpy`.
  - `tests/test_e2e.py` (marked `@pytest.mark.e2e`):
    - Real headless browser execution (Edge/Chrome/Chromium) verifying generated PDF has `>= 1` page via PyMuPDF.
    - E2E tests for Thai Unicode + Markdown table + code block rendering.
    - E2E tests for `--json` output schema with real browser.
    - E2E tests for stdin `-` with real browser.
    - E2E tests for default output path resolution.
    - E2E tests for missing image suppression warning.
    - Automatic graceful skip when no compatible browser is found.
  - `tests/test_ssot_boundary.py`:
    - Verifies zero duplication of core functions (`def render_pdf`, `def build_css`, `def _sanitize_html`, `class HtmlSanitizer`, etc.) across `src/`, `skills/`, and `tests/`.
    - Verifies no legacy imports of Tk `app` or top-level `converter`.
    - Verifies `src/mdtopdf_skill/cli.py` only imports from `mdtopdf_core`, `mdtopdf_skill`, or Python standard library.
- Verified test suite passes cleanly with **100% statement coverage** on `src/mdtopdf_skill`:
  ```
  Name                            Stmts   Miss  Cover   Missing
  -------------------------------------------------------------
  src\mdtopdf_skill\__init__.py       3      0   100%
  src\mdtopdf_skill\cli.py          209      0   100%
  -------------------------------------------------------------
  TOTAL                             212      0   100%
  ```
  Total tests: 177 passed in ~9.5s.
- Verified `ruff check .` passes with 0 issues.
- Verified `mypy src tests` passes with 0 issues across all 8 source files.
- **CLI bugs found:** None. The CLI implementation adheres strictly to the §5 contract and handles all error conditions correctly.

### §8.4 Review Agent – Completed (Review Round 1)
- [x] **§8.4 Review Agent:** Adversarial review across SSOT, §5 contract, path-traversal/security, cross-platform behavior, `SKILL.md` prompt-injection/trigger quality, packaging, and CI; record findings in `REVIEW.md`.

---

## §8.4 Review Agent – Audit Completed (Review Round 1)
**Date:** 2026-11-12

### Release Recommendation: **NO-GO (Conditional on fixing 2 High & 3 Medium findings)**

### Summary of Audit
- Completed an adversarial QA and security review across the entire repository without modifying source files or fixing code directly per §8.4.
- Generated comprehensive review report in `REVIEW.md` matching the format and standards of upstream `e:\Antigravity-Projects\mdtopdf\REVIEW.md`.
- Verified 100% statement coverage (177 tests passing in `9.37s`), clean `ruff` and `mypy` strict checks, zero Bandit security alerts, and successful fresh virtual environment installation from the upstream `v1.2.0` tag.
- Confirmed zero Single Source of Truth (SSOT) boundary violations: zero conversion logic duplication and zero unauthorized imports outside `mdtopdf_core`.

### Findings Identified (8 Total: 0 Critical, 2 High, 3 Medium, 3 Low)
- **F-01 [High]** `src/mdtopdf_skill/cli.py:384-396`: Input file overwrite & destruction when `-o` matches `INPUT.md` (unrecoverable data loss).
- **F-02 [High]** `src/mdtopdf_skill/cli.py:438-445`, `skills/md-to-pdf/SKILL.md:21-26`: Unsanitized `suppressed_images` printed to `stderr` allows multi-line prompt injection & ANSI escape injection into agent context.
- **F-03 [Medium]** `src/mdtopdf_skill/cli.py:380-396`: Passing an existing directory to `-o` triggers browser render failure (exit code 4) instead of upfront argument validation error (exit code 2).
- **F-04 [Medium]** `src/mdtopdf_skill/cli.py:397-399`: Non-existent `--base-dir` path is silently accepted instead of failing with exit code 2.
- **F-05 [Medium]** `skills/md-to-pdf/SKILL.md:36-38`: Example recommends `cat ... | mdtopdf -` which fails on Windows `cmd.exe` and corrupts UTF-8 Thai/Unicode under PowerShell 5.1 ANSI decoding.
- **F-06 [Low]** `src/mdtopdf_skill/cli.py:471-477`: Naive substring check `"--json" in effective_argv` produces unintended JSON on validation failures when `--json` is passed as an option value or after `--`.
- **F-07 [Low]** `src/mdtopdf_skill/cli.py:405-410, 428-436`: Production code contains test-mocking identity checks (`is _DEFAULT_RENDER_PDF`), causing redundant browser searches in production.
- **F-08 [Low]** `.github/workflows/ci.yml:1-44`: CI workflow omits explicit least-privilege `permissions: contents: read` block.

### Next Steps for Dev & Test Agents
- [x] **§8.2 Dev Agent:** Implement fixes for P0 (F-01, F-02), P1 (F-03, F-04), P2 (F-05), and P3 (F-06, F-07, F-08) per suggested diffs in `REVIEW.md`.
- [x] **§8.3 Test Agent:** Add regression tests in `tests/test_cli_execution.py` covering input/output path collisions, directory output, non-existent base-dir rejection, `--json` option-value disambiguation, and stderr prompt sanitization.
- [x] **§8.4 Review Agent:** Conduct Re-Review (Round 2) to confirm all High, Medium, and Low findings are resolved.

---

## §8.4 Re-Review (Round 2) – Completed (GO)
**Date:** 2026-11-12

### Summary
- All 8 findings (`F-01` through `F-08`: 2 High, 3 Medium, 3 Low) have been remediated across `src/mdtopdf_skill/cli.py`, `skills/md-to-pdf/SKILL.md`, `.github/workflows/ci.yml`, and `tests/test_cli_execution.py`.
- **182 passed in `9.34s`** with **100% statement coverage** (`231/231` statements across `src/mdtopdf_skill/`).
- `ruff check .` and `mypy src tests` pass with 0 issues.
- **Release Recommendation:** **GO** (0 open High/Medium/Low findings).
