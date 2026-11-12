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
- [ ] **SSOT Compliance (§2–§3):** Import only from `mdtopdf_core` (`render_pdf`, `ConversionOptions`, `ConversionResult`, `MARGIN_PRESETS`, `find_browser_executable`). Never import `app` or `converter`, and never duplicate conversion/CSS/sanitization logic.
- [ ] **Testable Structure (§8.2):** Keep `main(argv: list[str] | None = None) -> int` thin with separate argument-parsing and execution helper functions.
- [ ] **Flags & Defaults (§5):**
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
- [ ] **Encoding & Windows I/O (§5):** Read Markdown input as `utf-8-sig` (UTF-8 with BOM tolerated). Ensure `stdout`/`stderr` are UTF-8 safe on Windows (e.g. `reconfigure(encoding="utf-8", errors="replace")` where applicable).
- [ ] **Exit Codes (§5):**
  - `0`: Success (PDF exists, size > 0, `page_count >= 1`).
  - `2`: Bad CLI arguments, out-of-range/NaN/inf numbers, missing input file, or `-` without `--base-dir`.
  - `3`: No browser found (`FileNotFoundError` from core / `find_browser_executable`); error message **must mention `MDTOPDF_BROWSER`**.
  - `4`: Render failure (`RuntimeError` or unexpected conversion exception from core).
  - `5`: Output PDF missing, empty (`0` bytes), or `page_count == 0`.
- [ ] **Suppressed Images Warning (§5–§6):** When `result.suppressed_images` is non-empty, emit a clear warning on `stderr` (and include the list in `--json` output) while still returning exit code `0`.

#### 2. Antigravity Skill Documentation (`skills/md-to-pdf/SKILL.md` per §7)
- [ ] Keep frontmatter `name: md-to-pdf` and `description` specifying when to use (convert/export/print Markdown to PDF, Thai/Unicode docs, local images) and when not to use (editing PDFs, DOCX, LaTeX math).
- [ ] Document concise agent workflow:
  1. Check CLI availability (`mdtopdf --version`).
  2. Run conversion with `--json`.
  3. Inspect `suppressed_images` and `pages` in JSON output.
  4. Report output path (and any suppressed-image warnings) to the user.
- [ ] Add troubleshooting table keyed by exit codes (`0`, `2`, `3`, `4`, `5`).
- [ ] Include 2–3 copy-paste CLI examples.
- [ ] Document known upstream limitations (§6): (A) local images outside `base_dir` dropped unless `--allow-outside-base-dir` is set; (B) LaTeX `$...$` printed raw.

#### 3. Subsequent Role Queues
- [ ] **§8.3 Test Agent:** Expand `tests/` to cover all CLI flags, validation edge cases (`NaN`/`inf`), exit codes (`0`, `2`, `3`, `4`, `5`), `--json` schema, `stdin`, UTF-8 BOM, Windows paths with spaces/Unicode, `@pytest.mark.e2e` browser tests, and SSOT guard test (no copied `def render_pdf` / `def build_css`).
- [ ] **§8.4 Review Agent:** Adversarial review across SSOT, §5 contract, path-traversal/security, cross-platform behavior, `SKILL.md` prompt-injection/trigger quality, packaging, and CI; record findings in `REVIEW.md`.
