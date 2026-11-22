# Changelog

## 2.0.0

- Slimmed built-in print themes to `classic` (default, with `page_numbers: true`) and `paper`, removing `office`, `student`, `tech-spec`, `executive`, and `minimal`.
- Removed `--list-themes` CLI flag (made `INPUT.md` required), theme-only metadata fields (`course`, `instructor`, `student`, `status`, `version`, `owner`), and legacy `.station-block` wrapping.
- Moved `pillow` from runtime dependencies to `[project.optional-dependencies].dev`.
- Switched Markdown parsing from `markdown` to `markdown-it-py` (CommonMark + GFM tables/strikethrough) with 2/3-space nested ordered-list normalization, display-math (`$$...$$`) list continuity, and self-contained TOC and footnote rendering.
- Raised trailing orphan page detection threshold in `autofit` to `<= 6` body lines or `< 15%` fill ratio.
- Allowed tight pure-number `$...$` inline math (`$20.17$`, `$42$`) to render via KaTeX while preserving currency prose and ranges (`$5 and $10`, `$1,234.50`, `$5-$10`) as literal text.
- Added `break-inside: avoid` / `page-break-inside: avoid` to `h1`–`h4` headings in `classic` and `paper` themes to prevent multi-line heading splits across pages.
- Updated `skills/md-to-pdf/SKILL.md` Self-Healing Verification Loop to check `last_page_fill_ratio` and perform balanced `--preview-dir` visual spot-checks.
- Replaced stale v1 handoff docs with `CHANGELOG.md`, `docs/DECISIONS.md`, `AGENTS.md`, and `Makefile`.
