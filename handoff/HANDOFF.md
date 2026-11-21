> **SUPERSEDED:** This v0.1.0–v0.2.0 `mdtopdf_core` wrapper handoff has been **superseded** by [`handoff/IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) (`mdtopdf-skill` v1.0.0 standalone decoupled architecture). Retained for historical reference only.

---

# Handoff: `mdtopdf-skill` – Standalone CLI + Antigravity Skill

> Originally written in the legacy upstream repo (`e:\Antigravity-Projects\mdtopdf`, GitHub `DelMiiYa/Markdown2PDF`) as `docs/HANDOFF.md`, now stored under `handoff/HANDOFF.md` and superseded by `handoff/IMPLEMENTATION_PLAN.md` for `v1.0.0`.

## 1. Goal
A standalone repo that ships a **CLI (`mdtopdf`) and an Antigravity skill** (`SKILL.md`) so an agent can convert Markdown to a print-ready PDF headlessly with zero external `mdtopdf_core` dependency.

## 2. Standalone Architecture & Boundaries (v1.0.0)
- **Self-contained engine (`src/mdtopdf_skill/`):** All conversion logic lives directly inside `src/mdtopdf_skill/` (`models.py`, `frontmatter.py`, `themes.py`, `sanitizer.py`, `markdown_pipeline.py`, `cdp_browser.py`, `autofit.py`, `renderer.py`, `cli.py`) using PyPI-only runtime dependencies (`markdown`, `pymupdf`, `pillow`, `pyyaml`, `pygments`) and bundled offline Mermaid/KaTeX assets under `src/mdtopdf_skill/assets/`. There is **zero** dependency on `mdtopdf_core` or `Markdown2PDF`.
- **Architecture boundary enforcement (`tests/test_architecture_boundary.py`, replacing the deleted `tests/test_ssot_boundary.py`):**
  - Forbids importing `mdtopdf_core`, `markdown2pdf`, `tkinter`, `app`, `converter`, or `fitz` anywhere in `src/` or `tests/`.
  - Forbids external CDN URLs (`cdn.jsdelivr.net`, `unpkg.com`, `cdnjs.cloudflare.com`, `fonts.googleapis.com`) in `src/` or `skills/`.
  - Enforces decoupling (`markdown_pipeline.py` has no module-level `themes` import) and keeps `ThemeSpec` / `THEMES` centralized in `themes.py` and `models.py`.
  - Enforces per-render CSP cryptographic `nonce` (`secrets.token_hex`) and `<link rel="stylesheet">` loading for `katex.min.css`.
  - `mdtopdf_skill` must remain completely GUI-free.

### Public API (`from mdtopdf_skill import ...`)
| Name | Purpose |
| :-- | :-- |
| `render_pdf(md_text, base_dir, output_pdf_path, options=None, *, frontmatter=None, md_body=None) -> ConversionResult` | Main entry. Raises `FileNotFoundError` (no browser) and `CdpError` / `RuntimeError` (browser failure). |
| `ConversionOptions` | `theme="office"`, `lang="th"`, `font_size_pt=9.5`, `line_height=1.45`, `spacing_scale=1.0`, `margin_css="15mm 16mm"`, `page_size="A4 portrait"`, `img_max_width=90`, `img_max_height=70`, `suppress_missing_images=True`, `keep_blocks_together=True`, `accent_color="#0f3d7a"`, `title="Markdown Document"`, `page_numbers=True`, `header=None`, `footer=None`, `toc=False`, `frontmatter_css=""`, `cli_css=""`, `auto_fit=True`, `strict=False`, `allow_outside_base_dir=False`, `preview_dir=None`, `preview_dpi=144` |
| `ConversionResult` | `output_pdf_path`, `theme`, `page_count`, `embedded_images`, `suppressed_images: list[str]`, `warnings: list[str]`, `layout_issues: list[LayoutIssue]`, `mermaid_errors: list[MermaidError]`, `math_errors: list[MathError]`, `adjustments_applied: list[AdjustmentRecord]`, `last_page_fill_ratio: float`, `preview_images: list[str]`, `elapsed_seconds: float` |
| `THEMES` | 7 built-in print themes: `office` (default), `student`, `paper`, `tech-spec`, `executive`, `minimal`, `classic` |
| `MARGIN_PRESETS` | Keys: `compact` / `standard` / `comfortable` / `wide` → CSS margin strings |
| `PAGE_SIZE_PRESETS` | Keys: `A4`, `Letter`, `Legal` (`portrait` / `landscape`) → `(width_mm, height_mm)` |
| `find_browser_executable()` | Honours `MDTOPDF_BROWSER`, then Edge/Chrome/Chromium |
| `parse_frontmatter(md_text)` | Extracts YAML frontmatter into `(FrontmatterData, md_body)` |

Runtime needs: Edge, Chrome or Chromium installed on the host. Python deps (`markdown`, `pymupdf`, `pillow`, `pyyaml`, `pygments`) and offline Mermaid/KaTeX assets are bundled/declared in `pyproject.toml`.

## 3. How the new repo consumes the core
Pin by git tag in `pyproject.toml`:
```toml
dependencies = ["markdown2pdf @ git+https://github.com/DelMiiYa/Markdown2PDF.git@v1.2.0"]
```
- Use the tag cut after this refactor lands (the dev agent must confirm the tag exists; if not, ask the user to tag upstream).
- (Historical) Caveat: the old upstream `markdown2pdf` distribution also installed the Tk `app` module. Moot since v1.0.0 removed the dependency.
- Never `from app import ...` and never `import converter`. The old top-level `converter` module no longer exists.

## 4. Repo layout to scaffold
```
mdtopdf-skill/
  pyproject.toml            # name=mdtopdf-skill, console script `mdtopdf = mdtopdf_skill.cli:main`
  src/mdtopdf_skill/
    __init__.py
    cli.py                  # (historical) was a thin wrapper over mdtopdf_core.render_pdf; v1.0.0 is self-contained
  skills/md-to-pdf/
    SKILL.md                # frontmatter name/description + usage; follow Antigravity `plugin`/`agy-customizations` skill docs
  tests/
  handoff/HANDOFF.md        # this file (superseded by handoff/IMPLEMENTATION_PLAN.md)
  README.md
  .github/workflows/ci.yml  # pytest + ruff + mypy on Windows and Linux
```
The dev agent should check the Antigravity `plugin` and `agy-customizations` skills for the exact plugin manifest and layout, then adjust.

## 5. CLI contract
```
mdtopdf INPUT.md [-o OUT.pdf] [--base-dir DIR]
        [--font-size PT] [--line-height N] [--margin {compact,standard,comfortable,wide}|"12mm 14mm"]
        [--img-max-width PX] [--img-max-height PX]
        [--keep-missing-images] [--no-keep-together] [--accent "#hex"] [--title TEXT]
        [--allow-outside-base-dir] [--json] [--quiet]
```
- Defaults: output = input with `.pdf` extension. `--base-dir` defaults to the input file's folder. `-` reads stdin (then `--base-dir` is required).
- Read the input as UTF-8 (BOM tolerated). Use `-X utf8`-safe output on Windows.
- `--json` prints one JSON object to stdout: `{"ok":true,"output":..., "pages":..., "embedded_images":..., "suppressed_images":[...], "elapsed_seconds":...}`. Errors go to stderr, or as `{"ok":false,"error":...,"code":...}` with `--json`.
- Exit codes: `0` ok · `2` bad args or missing input · `3` no browser found (message must mention `MDTOPDF_BROWSER`) · `4` render failure · `5` output PDF empty or 0 pages.
- Suppressed images produce a **warning** (stderr, and in JSON), not a failure. The agent must be told so.
- `--allow-outside-base-dir` is off by default (path-traversal protection) and the help text must warn about that. Do not weaken UNC or `file://host` blocking, which is in the core.
- Validate numbers before calling the core (font size 6–72, line height 1–3, reject NaN/inf).

## 6. Known upstream limitations (do not paper over in the CLI)
Documented in the upstream `HANDOFF.md`: (A) absolute `file:///` images outside `base_dir` are silently dropped unless `allow_outside_base_dir=True`; (B) LaTeX `$...$` is printed raw. The CLI should surface (A) via the suppressed-image warning and mention both in `SKILL.md` "Limitations". Fixes belong upstream.

## 7. SKILL.md requirements
- Frontmatter `name: md-to-pdf` plus a `description` that says **when** to use it (convert, export, print Markdown to PDF; Thai/Unicode docs; local images) and when **not** to (editing PDFs, DOCX, LaTeX math).
- Steps: check the CLI is installed (`mdtopdf --version`) → run with `--json` → read `suppressed_images` and `pages` → report the output path to the user.
- Troubleshooting table keyed on the exit codes. Include 2-3 copy-paste examples.
- Keep it short. Put detailed flag docs in README, not SKILL.md.

## 8. Team and role briefs
*(Historical v0.1.0 role briefs below; v1.0.0 architecture and module layout are defined in [`handoff/IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).)*
Run each in a **separate conversation**. They share the repo and communicate through git commits and `handoff/STATUS.md` (a short log each role appends to). Order: Lead → Dev → Test → Review (Test and Review can start once Dev has a first commit).

### 8.1 Lead / Scaffold agent
> You own scaffolding `mdtopdf-skill` per `handoff/HANDOFF.md` §3–4. Create the repo skeleton, `pyproject.toml`, empty modules, CI workflow, `README.md` and `handoff/STATUS.md`. (Historical note: v0.1.0 used `mdtopdf_core`, which was completely removed in v1.0.0.) Done when: a fresh clone installs, `pytest` runs, CI file is valid, and `handoff/STATUS.md` lists open tasks.
>
> **Note on work already done (see `handoff/STATUS.md`):**
> - Git repo is initialized on `main`, and this file is at `handoff/HANDOFF.md`.
> - `.gitignore`, `pyproject.toml`, and `plugin.json` (Antigravity plugin manifest at repo root) are already created—review and keep them.
> - **Still to do in §8.1:** create `src/mdtopdf_skill/__init__.py`, stub `src/mdtopdf_skill/cli.py`, stub `skills/md-to-pdf/SKILL.md`, `tests/`, `.github/workflows/ci.yml`, `README.md`, update `handoff/STATUS.md` with open §5/§7 tasks, verify fresh `.venv` install + `pytest`/`ruff`/`mypy`, and make the initial git commit. Do not spawn subagents.

### 8.2 Dev agent
> *(Historical v0.1.0 brief; v1.0.0 standalone engine lives in `src/mdtopdf_skill/`.)* Implement `src/mdtopdf_skill/cli.py` and `skills/md-to-pdf/SKILL.md` with `main(argv) -> int`, separate parsing and run functions, `--version`, clean `ruff`/`mypy`, and `handoff/STATUS.md` logging.

### 8.3 Test agent (Standalone v1.0.0)
> Write the test suite across `tests/` without weakening production security contracts (report bugs via `handoff/STATUS.md`). Cover:
> - **Unit & Contract Suites:** `models.py` & `frontmatter.py` (`tests/test_frontmatter.py`), all 7 themes (`tests/test_themes.py`), HTML/CSS sanitization & `base_dir` path containment (`tests/test_sanitizer.py`), Markdown masking/alerts/TOC/CSP nonce (`tests/test_markdown_pipeline.py`), stdlib CDP browser discovery/WebSocket framing/Fetch guard (`tests/test_cdp_browser.py`), PyMuPDF body-rect orphan detection & table auto-fit (`tests/test_autofit.py`), and CLI argument parsing, 4-layer precedence, stdin, UTF-8/BOM, Windows Unicode/spaces, JSON schemas, and exit codes `0`/`2`/`3`/`4`/`5`/`6` (`--strict`) (`tests/test_cli_args.py`, `tests/test_cli_execution.py`).
> - **Architecture Boundary (`tests/test_architecture_boundary.py`, replacing the deleted `tests/test_ssot_boundary.py`):** Enforce zero imports of `mdtopdf_core`, `markdown2pdf`, `tkinter`, `app`, `converter`, or `fitz`; zero CDN URLs; lazy-only `themes` usage in `markdown_pipeline.py`; per-render CSP `nonce` + `<link rel="stylesheet">` KaTeX; and single-source theme definitions.
> - **Packaging & Opaque-Box E2E (`tests/test_e2e_requirements.py`, `tests/test_e2e_tiers.py`, `tests/test_e2e.py`):** Verify all 23 vendored asset SHA-256 checksums, `pyproject.toml` PyPI-only runtime deps (`bandit` in `dev`), and real headless browser tests marked `@pytest.mark.e2e` (skipping cleanly when no browser is installed on non-CI hosts). Done when: the suite passes on Windows and Linux in CI.

### 8.4 Review agent
> Do an adversarial review of the whole repo; don't fix things yourself. Check: architecture boundary compliance (`tests/test_architecture_boundary.py`), CLI/frontmatter/theme/autofit contract conformance, security (XSS/CSP, CSS sanitization, path traversal via `--base-dir` and `--allow-outside-base-dir`, CDP `127.0.0.1` binding + `Fetch` guard + cleanup, output-path overwrite, stdin handling, shell/subprocess misuse), cross-platform behaviour, `SKILL.md` accuracy and trigger quality (prompt-injection surface from document content), packaging (fresh-venv wheel install, bundled assets, entry point), and CI correctness. Write findings to `handoff/REVIEW.md` with IDs, severity and `file:line`. Re-review after fixes. Done when: no open High/Medium findings.

## 9. Definition of done (whole project)
1. Fresh clone → `pip install .` → `mdtopdf sample.md --json` produces a valid PDF.
2. Skill folder loads in Antigravity and an agent can convert a file using only `SKILL.md`.
3. Tests, ruff and mypy are green in CI on Windows and Linux. The Review agent has no open High/Medium findings.
4. (Historical) Nothing from `mdtopdf_core` is duplicated; the core pin no longer exists as of v1.0.0.
