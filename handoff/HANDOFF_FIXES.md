# Handoff: Fixes from the uncommitted-work review

Date: 2026-11-21. Context: v1.0.0 standalone rewrite (no `mdtopdf_core`). Last test run: 660 passed, 2 failed (both environmental, see task 1).

Older handoff material lives in this folder: `HANDOFF.md`, `STATUS.md`, `IMPLEMENTATION_PLAN.md`, `REVIEW.md`. `HANDOFF.md` is outdated (see task 3).

## Status (updated 2026-11-21)

| Task | Status |
|---|---|
| 1 Environment and tests | Done. 686 passed in .venv |
| 2 bandit to dev extra | Done |
| 3 Stale docs | Done. HANDOFF.md marked superseded and historical lines fixed |
| 4 Packaging | Done. Wheel contains all assets, 20 fonts and licences |
| 5 .gitattributes | Done |
| 6 Security | Done. --no-sandbox is now opt-in via MDTOPDF_NO_SANDBOX; CDP port is loopback-only; temp profile is cleaned up. Sanitizer tests exist (test_sanitizer.py) |
| 7 Split large files | Not done (optional) |

## Tasks

### 1. Fix the environment, then re-run tests (blocker)
- The global Python 3.13 site-packages contains a stale `mdtopdf_skill` and the legacy `mdtopdf_core`. Plain `pytest` imports the stale copy and fails at collection.
- Failing tests, both caused by this:
  - `tests/test_smoke.py::test_scaffold_imports` (asserts `mdtopdf_core` is not importable)
  - `tests/test_e2e_requirements.py::TestPyprojectAndPackaging::test_pyproject_metadata_and_zero_legacy_deps` (cause not confirmed; check its output)
- Steps:
  1. `pip uninstall mdtopdf_core mdtopdf_skill markdown2pdf` in the global Python.
  2. Use `.venv`: `.venv\Scripts\activate; pip install -e .[dev]`.
  3. `pytest -q`. The full run takes about 4.5 minutes, so don't pipe it through `Select-Object -Last`.
- Done when: all tests pass with no `PYTHONPATH` workaround.

### 2. Move `bandit` out of runtime dependencies
- `pyproject.toml` lists `bandit>=1.7,<2.0` under `dependencies`.
- Move it to `[project.optional-dependencies] dev`. Make sure CI installs the dev extra before `bandit -r src/`.
- Update the test that checks dependencies if it expects bandit.

### 3. Update stale docs
- `handoff/HANDOFF.md` §2 and §8.3 still describe the removed `mdtopdf_core` single-source-of-truth rule and `tests/test_ssot_boundary.py`, which was deleted. Rewrite them for the standalone design (`tests/test_architecture_boundary.py`).
- Check `README.md` and `skills/md-to-pdf/SKILL.md` for the same stale references.
- Paths were just moved from `docs/` to `handoff/`. `cli.py` (docstring), `tests/test_e2e_requirements.py` and `handoff/REVIEW.md` were updated. Grep for any remaining `docs/` references.

### 4. Verify packaging
- `python -m build`, then list the wheel contents.
- Confirm `assets/katex/*` and `assets/katex/fonts/*.woff2` are included.
- Confirm the KaTeX licence text is bundled. Add it if missing.
- Install the wheel in a clean venv and run a conversion with `$$...$$` math.

### 5. Add `.gitattributes`
- Git warns about LF being converted to CRLF on every file. Add a policy, for example `* text=auto eol=lf`, with `*.woff2 binary`.
- Renormalise: `git add --renormalize .`.

### 6. Security audit
- `src/mdtopdf_skill/sanitizer.py` (748 lines). Test `javascript:`, `data:` and `vbscript:` URLs, SVG and Mermaid output, event-handler attributes, and `<style>`/`@import`.
- `src/mdtopdf_skill/cdp_browser.py` (971 lines). Check the following:
  - The debug port binds to 127.0.0.1 only.
  - The temp profile is deleted on failure.
  - The browser process is killed on timeout or exception.
  - Local file access from the rendered page is limited to the intended assets.
- Add regression tests for any issue found.

### 7. Optional maintainability
- `themes.py` (1,721 lines) and `markdown_pipeline.py` (1,037 lines) are large. Consider moving theme CSS into `assets/` files or splitting by concern. Don't do this before tasks 1–6 are finished.

## Housekeeping done in this session
- Deleted `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `build/`, `.coverage` and all `__pycache__`.
- Kept `.venv` (it's in `.gitignore`).
- `docs/` is removed. Its files and `REVIEW.md` moved to `handoff/`.
- Nothing is committed yet.
