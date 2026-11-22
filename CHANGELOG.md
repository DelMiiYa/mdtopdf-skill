# Changelog

## 2.0.0

- Slimmed built-in print themes to `classic` (default, with `page_numbers: true`) and `paper`, removing `office`, `student`, `tech-spec`, `executive`, and `minimal`.
- Removed `--list-themes` CLI flag (made `INPUT.md` a required positional argument) and removed theme-specific metadata fields (`course`, `instructor`, `student`, `status`, `version`, `owner`) and legacy `.station-block` wrapping.
- Moved `pillow` from runtime dependencies to `[project.optional-dependencies].dev`.
- Replaced stale v1 handoff docs with `CHANGELOG.md`, `docs/DECISIONS.md`, `AGENTS.md`, and `Makefile`.
