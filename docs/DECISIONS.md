# Architecture Decisions

- **Two themes only (`classic` and `paper`):** Keeping only `classic` (clean sans-serif default) and `paper` (serif academic layout with booktabs tables) eliminates theme-variant CSS/header sprawl while covering general and academic documents.
- **Localhost CDP headless renderer (`127.0.0.1`):** `cdp_browser.py` drives Microsoft Edge, Google Chrome, or Chromium over a localhost (`127.0.0.1`) WebSocket using `MDTOPDF_BROWSER` for executable override, `MDTOPDF_TIMEOUT` (default `120`s) for runtime timeouts, and exits with `EXIT_NO_BROWSER` (`3`) when no browser binary is found.
- **Vendored offline Mermaid & KaTeX assets:** `mermaid.min.js`, `katex.min.js`, `katex.min.css`, and 20 `.woff2` fonts are bundled under `src/mdtopdf_skill/assets/` and lazy-injected under a per-render cryptographic CSP `nonce` so conversions make zero external network requests.
- **CommonMark/GFM parser (`markdown-it-py`):** Selected for v2.0.0 (with `table`, `strikethrough`, self-contained TOC/footnotes, and a lightweight ordered-list indent pre-pass) to support standard 2/3-space nested lists and tight lists without breaking list continuity around display math.
- **Trailing orphan threshold (`<= 6` lines or `< 15%` fill):** `autofit.py` flags sparse final pages below 15% body fill or at most 6 body lines (without images/vector diagrams) to trigger bounded auto-compaction.
- **No runtime `pillow` dependency:** Preview PNGs are rendered directly via `pymupdf` (`page.get_pixmap`), keeping `pillow` in `dev` dependencies for test fixtures only.
