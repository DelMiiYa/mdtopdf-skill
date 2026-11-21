# Third-Party Licenses & Vendored Offline Assets

This directory (`src/mdtopdf_skill/assets/`) bundles pinned, standalone, offline third-party browser assets so `mdtopdf-skill` renders Mermaid diagrams and KaTeX math formulas with **zero external network requests**.

## Vendored Components Summary

| Package | Version | License (SPDX) | Upstream Source URLs | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **mermaid** | `11.17.2` | `MIT` | jsDelivr: `https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js`<br>npm tarball: `https://registry.npmjs.org/mermaid/-/mermaid-11.17.2.tgz`<br>License: `https://cdn.jsdelivr.net/npm/mermaid@11.17.2/LICENSE` | Standalone IIFE browser bundle (`dist/mermaid.min.js`) |
| **katex** | `0.19.1` | `MIT` | jsDelivr JS: `https://cdn.jsdelivr.net/npm/katex@0.19.1/dist/katex.min.js`<br>jsDelivr CSS: `https://cdn.jsdelivr.net/npm/katex@0.19.1/dist/katex.min.css`<br>npm tarball: `https://registry.npmjs.org/katex/-/katex-0.19.1.tgz`<br>License: `https://cdn.jsdelivr.net/npm/katex@0.19.1/LICENSE` | **`.woff2`-only trim:** Retains `katex.min.js`, `katex.min.css`, and the 20 `fonts/*.woff2` files referenced by `katex.min.css`; omits legacy `.woff` and `.ttf` font files (Chromium supports `.woff2` natively) and `.map` files to minimize package size. |

---

## Verified SHA-256 Checksums (23 Vendored Files)

All checksums below were computed from the exact files vendored on disk under `src/mdtopdf_skill/assets/`:

| Relative Asset Path | Size (Bytes) | SHA-256 |
| :--- | ---: | :--- |
| `mermaid.min.js` | 3,572,661 | `581ed7d74bd9048d0e3a91363927d72ef22942d7722546b27f7cc29e35390eb8` |
| `katex/katex.min.js` | 273,081 | `fa140498b8cfc8302cbe5e7cb1bdbc1f952bc38b3fa258ede09e73edbf9a74bc` |
| `katex/katex.min.css` | 24,793 | `00e0b094171b07f58422075f469b4feac2b2f7bce1aeec943c08da4d697f4dcf` |
| `katex/fonts/KaTeX_AMS-Regular.woff2` | 28,076 | `0cdd387c9590a1a9f9794560022dbb59654a7d86f187aa0c81495ad42d3a7308` |
| `katex/fonts/KaTeX_Caligraphic-Bold.woff2` | 6,912 | `de7701e42cf1f4cf0b766c03fb27977207eee2f4fd5d76fa82188406da43ea4c` |
| `katex/fonts/KaTeX_Caligraphic-Regular.woff2` | 6,908 | `5d53e70ad607c2352162dec9e0923fb54ecdafaccbf604cd8dcf7d00facb989b` |
| `katex/fonts/KaTeX_Fraktur-Bold.woff2` | 11,348 | `74444efd593c005e3f4573b44524704c0af0a937fe911cca9e94068d0d140d3f` |
| `katex/fonts/KaTeX_Fraktur-Regular.woff2` | 11,316 | `51814d270d06ff0255dba0799994fa4d8c84d11f09951d47595f4abb1f3602dc` |
| `katex/fonts/KaTeX_Main-Bold.woff2` | 25,324 | `0f60d1b897938ec918c8ce073092411baf9438f6739465693ff18b0f9d20b021` |
| `katex/fonts/KaTeX_Main-BoldItalic.woff2` | 16,780 | `99cd42a3c072d918f2f44984a807cf7aa16e13545fd0875fc07c6c65f99e715b` |
| `katex/fonts/KaTeX_Main-Italic.woff2` | 16,988 | `97479ca6cce906abc961ecac96faa5f9ca2e61b8e7670d475826bcdee9a7c267` |
| `katex/fonts/KaTeX_Main-Regular.woff2` | 26,272 | `c2342cd8b869e01752a9321dc17213fc40d4d04c79688c1d43f2cf316abd7866` |
| `katex/fonts/KaTeX_Math-BoldItalic.woff2` | 16,400 | `dc47344dbb6cb5b655c8460d561f4df5f501b90c804ad3c6cec65fe322351ab1` |
| `katex/fonts/KaTeX_Math-Italic.woff2` | 16,440 | `7af58c5ec8f132a2ddde9027c6d7814decce4d3b822a11192a42a20e2e973264` |
| `katex/fonts/KaTeX_SansSerif-Bold.woff2` | 12,216 | `e99ae51144bf1232efcc1bfe5add36262c6866b0faab24fa75740e1b98577a62` |
| `katex/fonts/KaTeX_SansSerif-Italic.woff2` | 12,028 | `00b26ac825e2095056396e0553b8ac26d3f8ad158c3826e28b4c45b385c4714a` |
| `katex/fonts/KaTeX_SansSerif-Regular.woff2` | 10,344 | `68e8c73ef42afd3ccec58bf0fba302cce448938e7fc020a5e31f8a952eee1342` |
| `katex/fonts/KaTeX_Script-Regular.woff2` | 9,644 | `036d4e95149b69ff9bcc0cd55771efeb25ffa3947293e69acd78d5ac328c684b` |
| `katex/fonts/KaTeX_Size1-Regular.woff2` | 5,468 | `6b47c40166b6dbe21a5dfca7718413f2147fd2399be1ba605d8ad39cedf25dfe` |
| `katex/fonts/KaTeX_Size2-Regular.woff2` | 5,208 | `d04c54219f9eaec6d4d4fd42dfb28785975a4794d6b2fc71e566b9cd6db842dd` |
| `katex/fonts/KaTeX_Size3-Regular.woff2` | 3,624 | `73d591271b1604960cb10bb90fee021670af7297017e0e98480b332d11f51995` |
| `katex/fonts/KaTeX_Size4-Regular.woff2` | 4,928 | `a4af7d414440a1c1790825cfb700cf9cf43b0f2c4b04f0ebc523011ad9853ec0` |
| `katex/fonts/KaTeX_Typewriter-Regular.woff2` | 13,568 | `71d517d67827787cfabdf186914cc3358eda539e37931941f2b2fd4a21f68c0b` |

---

## Verbatim Upstream Licenses

### Mermaid (`mermaid@11.17.2`) — MIT License

```text
The MIT License (MIT)

Copyright (c) 2014 - 2022 Knut Sveidqvist

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### KaTeX (`katex@0.19.1`) — MIT License

```text
The MIT License (MIT)

Copyright (c) 2013-2020 Khan Academy and other contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
