# Agent Instructions

- Keep `mdtopdf-skill` simple, small, and offline-first; do not add extra runtime dependencies or external network calls.
- Only two print themes exist: `classic` (default) and `paper`.
- Keep all vendored Mermaid/KaTeX assets intact under `src/mdtopdf_skill/assets/`.
- Keep `markdown_pipeline.py` decoupled from `themes.py` at module import time.
- Run all four checks before finishing any change:
  - `ruff check .`
  - `mypy src tests`
  - `bandit -r src/`
  - `pytest -v`
