# Handoff: Slim-down + Maintainability Pass (mdtopdf-skill v1.0.0 → v2)

> Read this file first. It supersedes `HANDOFF.md`, `HANDOFF_FIXES.md`, `REVIEW.md`, and `STATUS.md` (those describe the retired `mdtopdf_core` version or are stale).

## Goal
Make the project small, simple and easy to maintain. The owner wants it "very simple yet efficient".

## Ground rules
- Keep only the **`default`** and **`paper`** themes. Remove the other 5 (student, executive, tech-spec, minimal, classic, office; confirm the exact list in `themes.py`).
- Do not add features. Delete code that only existed to support removed themes.
- Work on a branch `slim-v2`. Commit in small steps, one logical change per commit.
- Before finishing each step, run: `ruff check .`, `mypy src tests`, `pytest`. All must pass.
- Do not touch `handoff/mdtopdf-skill-improvements.md` content; use it as the bug source (see Phase 3).

## Phase 0: Safety (do first)
1. `git status`. Commit or stash the untracked `handoff/mdtopdf-skill-improvements.md` (commit it on `main` as "docs: add improvements notes").
2. `git tag v1.0.0` on the current `main` HEAD (rollback point).
3. `git checkout -b slim-v2`.

## Phase 1: Remove themes and theme-only options
- `src/mdtopdf_skill/themes.py` (1,721 lines): keep `default` and `paper` only. Simplify the registry.
- Update `cli.py` (`--theme` choices, defaults, help text), `models.py`, `frontmatter.py` (theme validation), `renderer.py` if they reference removed themes.
- Update `skills/md-to-pdf/SKILL.md`, `README.md`, `plugin.json` description to list only the 2 themes.
- Delete or trim tests in `tests/test_themes.py`, `tests/test_e2e_tiers.py`, `tests/test_cli_args.py` and others that reference removed themes. Keep coverage for the 2 remaining themes.
- Check `tests/test_architecture_boundary.py` still makes sense.

## Phase 2: Docs cleanup
- Create `CHANGELOG.md` (one line per change, start with v2 entries).
- Create `docs/DECISIONS.md`: short bullets, e.g. "only 2 themes", "CDP-based renderer", "Mermaid/KaTeX vendored in assets/".
- Create `AGENTS.md` (about 10 lines): keep it simple, only default and paper themes, run the 3 checks before finishing.
- Move the old `handoff/*.md` (except this file and the improvements doc) out of `main`: run `git tag v1-archive` on the commit before deletion, then `git rm`. Update the README so it doesn't link to deleted files.
- Add a `Makefile` or `justfile` with a `check` target (ruff, mypy, pytest). Optional: add a pre-commit hook for ruff.

## Phase 3: Fix known bugs (one commit each, each with a regression test)
Source: `handoff/mdtopdf-skill-improvements.md`.
1. **List parsing**: Python-`markdown` uses a 4-space model, so 2/3-space nested lists, tight lists after a paragraph, and lists around `$$` display math all break (`markdown_pipeline.py`, around line 933). Preferred fix: a small pre-processing normalizer (expand 2/3-space list indents to 4, insert a blank line before a list that follows a paragraph). Only switch to `markdown-it-py` if the normalizer proves fragile, and note the choice in `docs/DECISIONS.md`.
2. **Sparse trailing page**: `autofit.py` `inspect_pdf_pages` (around lines 159–164) only flags orphans at ≤3 lines or <8% fill. Raise the threshold (suggested ~20%) and make sure auto-compaction then triggers. Verify with a sample doc that previously ended at 9–19% fill.
3. **SKILL.md gaps**: read sections 3–5 of the improvements doc and apply any instruction fixes to `skills/md-to-pdf/SKILL.md`.

## Phase 4: Verify
- Full check suite green on Windows (CI also runs on ubuntu, Python 3.10 and 3.12).
- Convert a sample markdown with Mermaid, KaTeX, nested lists and tables in both themes, then open the PDFs. Confirm no `layout_issues` and no sparse last page.
- `pip install .` into a clean venv and run `mdtopdf --help` to confirm the wheel still includes the assets.

## Definition of done
- Only 2 themes exist anywhere in code, docs, tests and CLI help.
- `handoff/` contains only this file and the improvements doc (or neither, if the bugs are fixed and logged in the changelog).
- The two bugs have regression tests and pass.
- All checks pass and `CHANGELOG.md` is updated.
- Leave the branch unmerged and report a summary: files deleted, line-count reduction, test count.

## Out of scope (do not do without asking)
- Removing the vendored Mermaid/KaTeX assets.
- Merging or refactoring `models.py` and other large modules.
- Pushing, opening PRs, or merging to `main`.
