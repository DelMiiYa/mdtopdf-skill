# Handoff: `mdtopdf-skill` – CLI + Antigravity skill built on `mdtopdf_core`

> Written in the **upstream** repo (`e:\Antigravity-Projects\mdtopdf`, GitHub `DelMiiYa/Markdown2PDF`).
> Copy this file into the new repo as `docs/HANDOFF.md` and hand each role brief (§8) to a separate agent conversation.

## 1. Goal
A new repo that ships a **CLI (`mdtopdf`) and an Antigravity skill** (`SKILL.md`) so an agent can convert Markdown to a print-ready A4 PDF headlessly. The conversion logic is **not** re-implemented there.

## 2. SSOT rule (non-negotiable)
- The engine lives in upstream package **`mdtopdf_core`** (`mdtopdf_core/converter.py`). It is the only place conversion logic exists.
- The new repo **depends on it and never copies or forks it**. No vendored copy and no patched copy.
- Bugs or features in rendering (CSS, sanitising, image resolution, browser handling) are fixed **upstream**, released with a tag, and then the pin is bumped here.
- `mdtopdf_core` must stay GUI-free. Upstream enforces this in `tests/test_core_boundary.py`.

### Public API (`from mdtopdf_core import ...`)
| Name | Purpose |
| :-- | :-- |
| `render_pdf(md_text, base_dir, output_pdf_path, options=None) -> ConversionResult` | Main entry. Raises `FileNotFoundError` (no browser) and `RuntimeError` (browser failure). |
| `ConversionOptions` | `font_size_pt=9.5`, `line_height=1.45`, `margin_css="12mm 14mm"`, `page_size="A4 portrait"`, `img_max_width=90`, `img_max_height=70`, `suppress_missing_images=True`, `keep_blocks_together=True`, `accent_color="#0d47a1"`, `title`, `allow_outside_base_dir=False` |
| `ConversionResult` | `output_pdf_path`, `page_count`, `embedded_images`, `suppressed_images: list[str]`, `elapsed_seconds` |
| `MARGIN_PRESETS` | Keys: Compact / Standard / Comfortable / Wide → CSS margin strings |
| `find_browser_executable()` | Honours `MDTOPDF_BROWSER`, then Edge/Chrome/Chromium |
| `render_pdf_pages_to_pil` | GUI preview only. Don't use in the CLI. |

Runtime needs: Edge, Chrome or Chromium installed. Python deps (`markdown`, `PyMuPDF`, `Pillow`) come with the core.

## 3. How the new repo consumes the core
Pin by git tag in `pyproject.toml`:
```toml
dependencies = ["markdown2pdf @ git+https://github.com/DelMiiYa/Markdown2PDF.git@v1.2.0"]
```
- Use the tag cut after this refactor lands (the dev agent must confirm the tag exists; if not, ask the user to tag upstream).
- Caveat: the upstream distribution is named `markdown2pdf` and also installs the Tk `app` module. That is harmless as long as the CLI imports only `mdtopdf_core`. **Open decision for the user:** split upstream into a core-only distribution later.
- Never `from app import ...` and never `import converter`. The old top-level `converter` module no longer exists.

## 4. Repo layout to scaffold
```
mdtopdf-skill/
  pyproject.toml            # name=mdtopdf-skill, console script `mdtopdf = mdtopdf_skill.cli:main`
  src/mdtopdf_skill/
    __init__.py
    cli.py                  # argparse; thin wrapper over mdtopdf_core.render_pdf
  skills/md-to-pdf/
    SKILL.md                # frontmatter name/description + usage; follow Antigravity `plugin`/`agy-customizations` skill docs
  tests/
  docs/HANDOFF.md           # this file
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
Run each in a **separate conversation**. They share the repo and communicate through git commits and `docs/STATUS.md` (a short log each role appends to). Order: Lead → Dev → Test → Review (Test and Review can start once Dev has a first commit).

### 8.1 Lead / Scaffold agent
> You own scaffolding `mdtopdf-skill` per `docs/HANDOFF.md` §3–4. Create the repo skeleton, `pyproject.toml` with the pinned core dependency, empty modules, CI workflow, `README.md` and `docs/STATUS.md`. Confirm the upstream tag exists and installs (`pip install` in a fresh venv, then `python -c "import mdtopdf_core"`). Do not write CLI logic. Done when: a fresh clone installs, `pytest` runs (even if empty), CI file is valid, and STATUS.md lists the open tasks from §5 and §7.
>
> **Note on work already done (see `docs/STATUS.md`):**
> - Git repo is initialized on `main`, and this file is already at `docs/HANDOFF.md`.
> - `.gitignore`, `pyproject.toml` (pinned to `v1.2.0`), and `plugin.json` (Antigravity plugin manifest at repo root) are already created—review and keep them.
> - Upstream tag `v1.2.0` is confirmed pushed to `https://github.com/DelMiiYa/Markdown2PDF.git`.
> - **Still to do in §8.1:** create `src/mdtopdf_skill/__init__.py`, stub `src/mdtopdf_skill/cli.py`, stub `skills/md-to-pdf/SKILL.md`, `tests/`, `.github/workflows/ci.yml`, `README.md`, update `docs/STATUS.md` with open §5/§7 tasks, verify fresh `.venv` install + `import mdtopdf_core` + `pytest`/`ruff`/`mypy`, and make the initial git commit. Do not spawn subagents.

### 8.2 Dev agent
> Implement `src/mdtopdf_skill/cli.py` exactly per §5 and `skills/md-to-pdf/SKILL.md` per §7. SSOT rule §2 applies: import only from `mdtopdf_core`, no copied logic. Keep `cli.py` thin and testable (`main(argv) -> int`, with separate parsing and run functions). Add `--version`. Run ruff and mypy clean. Done when: all flags work end to end on a sample Thai + image + table `.md`, exit codes match the contract, and you've listed anything you couldn't do in STATUS.md.

### 8.3 Test agent
> Write the test suite for the CLI without modifying `cli.py` (report bugs to Dev via STATUS.md). Cover: arg parsing and validation (incl. NaN/inf, out-of-range), every exit code (mock `render_pdf` and `find_browser_executable` to raise), JSON schema, stdin mode, default output/base-dir, suppressed-image warning behaviour, UTF-8/BOM, and Windows paths with spaces and Unicode. Add a small number of real end-to-end tests marked `@pytest.mark.e2e` that skip when no browser is available, and assert the PDF has ≥1 page. Also add a test that the repo contains no copy of core code (grep for `def render_pdf`/`def build_css` outside the dependency). Report coverage. Done when: the suite passes on Windows, and Linux in CI.

### 8.4 Review agent
> Do an adversarial review of the whole repo; don't fix things yourself. Check: SSOT violations (any duplicated core logic, any import outside `mdtopdf_core`), contract conformance with §5, security (path traversal via `--base-dir` and `--allow-outside-base-dir`, output-path overwrite, stdin handling, shell/subprocess misuse), cross-platform behaviour, SKILL.md accuracy and trigger quality (would an agent pick it correctly? any prompt-injection surface from document content?), packaging (fresh-venv install, entry point), CI correctness. Write findings to `REVIEW.md` with IDs, severity and file:line, in the same style as upstream `REVIEW.md`. Re-review after fixes. Done when: no open High/Medium findings.

## 9. Definition of done (whole project)
1. Fresh clone → `pip install .` → `mdtopdf sample.md --json` produces a valid PDF.
2. Skill folder loads in Antigravity and an agent can convert a file using only `SKILL.md`.
3. Tests, ruff and mypy are green in CI on Windows and Linux. The Review agent has no open High/Medium findings.
4. Nothing from `mdtopdf_core` is duplicated, and the core pin is documented in the README with the bump procedure.
