---
name: md-to-pdf
description: Convert, export, or print Markdown documents (including Thai/Unicode text, tables, and local images) into print-ready A4 PDFs using the headless mdtopdf CLI. Do not use for editing existing PDFs, converting DOCX files, or rendering LaTeX math formulas.
---

# Markdown to PDF (`md-to-pdf`)

> **TODO (§8.2 Dev Agent):** Complete this skill documentation per `docs/HANDOFF.md` §7:
> - Steps: verify `mdtopdf --version` -> run `mdtopdf <input.md> --json` -> inspect `suppressed_images` and `pages` -> report output path.
> - Exit code troubleshooting table (`0`, `2`, `3`, `4`, `5`).
> - 2–3 copy-paste invocation examples.
> - Known upstream limitations (`allow_outside_base_dir=False` default path-traversal guard, raw `$...$` LaTeX math).
