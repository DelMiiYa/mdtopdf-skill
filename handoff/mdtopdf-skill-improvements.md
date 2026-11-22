---
title: "mdtopdf-skill & md-to-pdf Skill: Issues & Improvement Suggestions"
subtitle: "Findings from Multi-Theme Conversion of SUMMARY_TH.md (mdtopdf v1.0.0)"
date: "2026-11-22"
---

This document summarizes five technical and instructional gaps identified in `mdtopdf-skill` (`v1.0.0`) and `SKILL.md`, along with concrete suggestions for each.

---

## 1. CommonMark / GFM List Incompatibility

**Reference:** `mdtopdf_skill/markdown_pipeline.py` (line 933)

### Problem

`mdtopdf` compiles Markdown using Python-`markdown` (`extensions=["tables", "fenced_code", "codehilite", "footnotes", "toc", "sane_lists"]`) with a strict 4-space indentation model rather than a CommonMark/GFM parser. During conversion of `SUMMARY_TH.md`, this caused several structural failures while returning zero warnings in `--json`:

- **Collapsed nested sub-bullets:** Sub-items indented by 2 or 3 spaces under numbered items (Sections 1, 2.1, 2.2, 3, and 5) were not parsed as nested lists—they collapsed inline into the parent list item or flattened to top-level bullets.
- **Broken list continuation around display math:** A `$$...$$` block inside Section 1 item 4 broke list continuity, causing the following sub-bullet and numbered item 5 to merge into a single bullet.
- **Tight lists merged into prose:** Section 7's numbered list (`1.`–`7.`), which began immediately after a paragraph without a preceding blank line, collapsed into one dense paragraph.

### Suggestion

- Replace Python-`markdown` with a CommonMark/GFM-compliant parser (such as `markdown-it-py` with table/footnote/tasklist plugins) so standard 2- and 3-space nested lists and tight lists render as authored.
- Alternatively, add a pre-processing normalization pass before Python-`markdown` to expand 2/3-space list indentation to 4 spaces and insert blank lines before list blocks following paragraphs.

---

## 2. Sparse Trailing Page Threshold Is Too Low

**Reference:** `mdtopdf_skill/autofit.py` (`inspect_pdf_pages`, lines 159–164)

### Problem

`inspect_pdf_pages` only flags `is_trailing_orphan = True` when `body_line_count <= 3` or `last_page_fill_ratio < 0.08` (8%). As a result, documents with clearly awkward, nearly empty final pages passed with `"layout_issues": []` and triggered no auto-compaction:

- `paper` (`--margin compact --font-size 8.5`): Page 5 contained ~4.5 lines at the top (`last_page_fill_ratio = 0.0943`, 9.4%).
- `student`: Page 6 ended at `last_page_fill_ratio = 0.1204` (12.0%).
- `executive`: Page 6 ended at `last_page_fill_ratio = 0.1946` (19.5%).

### Suggestion

- Raise the default `is_trailing_orphan` / sparse-page threshold (for example, `last_page_fill_ratio < 0.15` or `body_line_count <= 6`) to trigger compaction or emit a `sparse_trailing_page` warning.
- Expose an optional CLI flag (such as `--orphan-threshold`) so users or agents can tune how aggressively trailing sparse pages are pulled back onto the preceding page.

---

## 3. Pure-Number `$...$` Math Heuristic False Negative

**Reference:** `mdtopdf_skill/markdown_pipeline.py` (`_PURE_CURRENCY_INNER_RE` on line 39, checked on line 495)

### Problem

To avoid misinterpreting currency values as LaTeX math, `_PURE_CURRENCY_INNER_RE` (`^\d[\d,.\s]*$`) skips any `$...$` span whose inner content contains only digits, commas, periods, and whitespace. However, when an author writes deliberate inline math containing a single number—such as `$20.17$` in Section 3 item 2 of `SUMMARY_TH.md`—the delimiters are left untouched and rendered as literal `$20.17$` in the final PDF with no diagnostic warning in `math_errors`.

### Suggestion

- Narrow the currency-skip heuristic so it only ignores spans containing internal whitespace or currency patterns across prose (e.g., `$10 ... $20`), while allowing tight single-token expressions like `$20.17$` with no adjacent spaces inside the delimiters—or at minimum emit a `warnings` entry when a `$...$` pair is skipped.
- Document the `$number$` currency rule clearly in user and skill documentation.

---

## 4. Gaps in `SKILL.md` Verification & Usage Guidance

**Reference:** `skills/md-to-pdf/SKILL.md` ("Self-Healing Verification Loop", lines 30–39)

### Problem

`SKILL.md` relies too heavily on `--json` error arrays and omits key heuristics needed for reliable output QA:

- **Under-triggered visual inspection:** Step 5 advises running `--preview-dir` only *"If warnings remain or visual verification is requested."* In practice, `--json` reported zero `layout_issues`, `mermaid_errors`, `math_errors`, and `warnings` even when lists collapsed inline, `$20.17$` printed literally, Thai table headers wrapped mid-word, and `paper` ended with a 9.4%-full final page.
- **Unused `last_page_fill_ratio` telemetry:** `mdtopdf --json` already reports `last_page_fill_ratio`, yet `SKILL.md` gives no guidance on acting on low values (such as `< 0.15`) when `trailing_orphan_page` is not triggered.
- **Undocumented Markdown parser constraints:** `SKILL.md` does not warn agents about Python-`markdown`'s 4-space nested list requirement, blank-line-before-list requirement, or the pure-number `$...$` math skip.

### Suggestion

- Add a rule to `SKILL.md` instructing the agent to check `last_page_fill_ratio` on multi-page PDFs and suggest or apply `--spacing-scale 0.85` (or `--margin compact`) when it falls below `0.15`.
- Recommend spot-checking at least page 1, table-heavy pages, and the final page via `--preview-dir` + `read_file` whenever visual quality matters, rather than treating `"layout_issues": []` as full visual sign-off.
- Document the 4-space nested list requirement, blank-line-before-list rule, and pure-number `$...$` currency behavior in a "Known Parser Quirks" note inside `SKILL.md`.

---

## 5. Wrapped Multi-Line Headings (`h2`/`h3`/`h4`) Split Across Page Boundaries

**Reference:** `mdtopdf_skill/themes.py` (heading styles for `h2`, `h3`, `h4`)

### Problem

In `themes.py`, heading styles (`h2`, `h3`, `h4`) enforce `break-after: avoid` without `break-inside: avoid`. When a multi-line heading precedes a chained `break-after: avoid` / `break-before: avoid` sequence and a large `break-inside: avoid` element (such as a `.mermaid-container`), Chromium's print pagination can split the heading across pages—stranding its first line on an otherwise empty page while moving the remaining line(s) and following content to the next page.

### Suggestion

- Add `break-inside: avoid; page-break-inside: avoid;` to heading styles (`h2`, `h3`, `h4`) in `themes.py` so Chromium cannot split a wrapped heading mid-element, preventing stranded single-line heading fragments on near-empty pages.

