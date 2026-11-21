(() => {
  "use strict";

  const OVERFLOW_TOLERANCE_PX = 1.5;
  const IMAGE_SETTLE_TIMEOUT_MS = 5000;
  const MAX_SNIPPET_LEN = 200;

  let mermaidInitialized = false;

  function parseIndex(rawValue, fallback) {
    if (typeof rawValue !== "string" || rawValue.trim() === "") {
      return fallback;
    }
    const parsed = Number.parseInt(rawValue, 10);
    return Number.isInteger(parsed) && parsed >= 0 ? parsed : fallback;
  }

  function formatErrorMessage(err) {
    if (err && typeof err.message === "string" && err.message.trim() !== "") {
      return err.message;
    }
    return String(err);
  }

  function makeSnippet(src) {
    const trimmed = String(src || "").trim();
    if (trimmed.length <= MAX_SNIPPET_LEN) {
      return trimmed;
    }
    return trimmed.slice(0, MAX_SNIPPET_LEN - 3) + "...";
  }

  async function waitForFonts() {
    if (document.fonts && document.fonts.ready) {
      try {
        await document.fonts.ready;
      } catch (_) {
        // Ignore font loading rejections; geometry inspection still proceeds.
      }
    }
  }

  function waitForSingleImage(img) {
    return new Promise((resolve) => {
      let settled = false;
      let timerId = null;

      const finish = () => {
        if (settled) {
          return;
        }
        settled = true;
        if (timerId !== null) {
          clearTimeout(timerId);
        }
        img.removeEventListener("load", onLoadOrError);
        img.removeEventListener("error", onLoadOrError);
        resolve();
      };

      const decodeAndFinish = () => {
        if (img.naturalWidth > 0 && typeof img.decode === "function") {
          img.decode().catch(() => {}).finally(finish);
        } else {
          finish();
        }
      };

      const onLoadOrError = () => {
        decodeAndFinish();
      };

      timerId = setTimeout(finish, IMAGE_SETTLE_TIMEOUT_MS);

      if (img.complete) {
        decodeAndFinish();
      } else {
        img.addEventListener("load", onLoadOrError, { once: true });
        img.addEventListener("error", onLoadOrError, { once: true });
      }
    });
  }

  async function waitForImages() {
    const imgs = Array.from(document.images || []);
    if (imgs.length === 0) {
      return;
    }
    await Promise.all(imgs.map((img) => waitForSingleImage(img)));
  }

  function renderMathError(el, tex, message) {
    el.textContent = "";
    const code = document.createElement("code");
    code.className = "math-error";
    code.setAttribute("title", message);
    code.textContent = tex !== "" ? tex : message;
    el.appendChild(code);
  }

  function renderAllMath() {
    const mathErrors = [];
    const nodes = Array.from(document.querySelectorAll(".math-inline, .math-display"));
    if (nodes.length === 0) {
      return mathErrors;
    }

    const hasKatex =
      typeof window.katex === "object" &&
      window.katex !== null &&
      typeof window.katex.render === "function";

    for (let seqIdx = 0; seqIdx < nodes.length; seqIdx++) {
      const el = nodes[seqIdx];
      if (el.dataset && el.dataset.mdtopdfRendered === "1") {
        continue;
      }

      const displayMode = el.classList.contains("math-display");
      const index = parseIndex(el.dataset ? el.dataset.mathIndex : undefined, seqIdx);
      const tex = el.textContent != null ? el.textContent : "";

      if (!hasKatex) {
        const msg = "KaTeX library is not loaded";
        renderMathError(el, tex, msg);
        if (el.dataset) {
          el.dataset.mdtopdfRendered = "1";
        }
        mathErrors.push({
          index,
          display_mode: displayMode,
          message: msg,
          tex,
        });
        continue;
      }

      try {
        window.katex.render(tex, el, {
          displayMode,
          throwOnError: true,
          strict: "warn",
        });
      } catch (err) {
        const msg = formatErrorMessage(err);
        renderMathError(el, tex, msg);
        mathErrors.push({
          index,
          display_mode: displayMode,
          message: msg,
          tex,
        });
      }

      if (el.dataset) {
        el.dataset.mdtopdfRendered = "1";
      }
    }

    return mathErrors;
  }

  function renderMermaidError(el, message) {
    el.textContent = "";
    el.classList.remove("mermaid-container");
    const box = document.createElement("div");
    box.className = "mermaid-error-box";

    const badge = document.createElement("div");
    badge.className = "mermaid-error-badge";
    badge.textContent = "Mermaid Render Error";

    const pre = document.createElement("pre");
    pre.textContent = message;

    box.appendChild(badge);
    box.appendChild(pre);
    el.appendChild(box);
  }

  const FORBIDDEN_MERMAID_TAGS =
    "script, iframe, object, embed, applet, link, meta, base, form, input, textarea, select, button";
  const ALLOWED_LINK_SCHEMES = new Set(["http", "https", "mailto"]);
  const SAFE_RASTER_DATA_URI_RE =
    /^data:image\/(?:png|jpe?g|gif|webp|bmp|avif)(?:;[a-z0-9.+_-]+(?:=[^;,]*)?)*,/i;
  const URI_CTRL_WS_RE = /[\x00-\x20\x7f-\x9f\s]+/g;
  const URI_SCHEME_RE = /^([a-z][a-z0-9+.-]*):/;
  const CTRL_CHAR_RE = /[\x00-\x1f\x7f-\x9f]/;
  const INLINE_STYLE_HAZARD_RE =
    /(?:url\s*\((?!\s*['"]?#)|(?:^|[^a-z0-9_-])(?:-(?:webkit|moz)-)?image-set\s*\(|(?:^|[^a-z0-9_-])src\s*\(|expression\s*\(|@import|behavior\s*:|-moz-binding\s*:|javascript:|vbscript:|\\)/i;
  const SVG_STYLE_NON_FRAG_URL_RE = /url\s*\((?!\s*['"]?#)(?:"[^"]*"|'[^']*'|[^)])*\)/gi;
  const SVG_STYLE_HAZARD_FUNC_RE =
    /(?:-(?:webkit|moz)-image-set|\bimage-set|\bsrc|\bexpression)\s*\((?:"[^"]*"|'[^']*'|[^)])*\)/gi;

  function isSafeRasterDataUri(rawValue) {
    if (typeof rawValue !== "string") {
      return false;
    }
    const trimmed = rawValue.trim();
    if (!trimmed || CTRL_CHAR_RE.test(trimmed)) {
      return false;
    }
    return SAFE_RASTER_DATA_URI_RE.test(trimmed);
  }

  function isSafeMermaidLinkUri(rawValue) {
    if (typeof rawValue !== "string") {
      return false;
    }
    const normalized = rawValue.replace(URI_CTRL_WS_RE, "").toLowerCase();
    if (!normalized) {
      return false;
    }
    if (normalized.startsWith("#")) {
      return true;
    }
    const match = URI_SCHEME_RE.exec(normalized);
    if (!match) {
      return false;
    }
    return ALLOWED_LINK_SCHEMES.has(match[1]);
  }

  function sanitizeInlineStyleValue(styleText) {
    if (typeof styleText !== "string" || !styleText.trim()) {
      return "";
    }
    const noComments = styleText.replace(/\/\*[\s\S]*?\*\//g, "");
    const safeDecls = [];
    const parts = noComments.split(";");
    for (let i = 0; i < parts.length; i++) {
      const decl = parts[i].trim();
      if (!decl || INLINE_STYLE_HAZARD_RE.test(decl)) {
        continue;
      }
      safeDecls.push(decl);
    }
    return safeDecls.join("; ");
  }

  function sanitizeSvgStyleText(cssText) {
    if (typeof cssText !== "string") {
      return "";
    }
    let cleaned = cssText.replace(/\/\*[\s\S]*?\*\//g, "");
    // Neutralize CSS backslash escapes so obfuscated @import/url() cannot bypass checks.
    cleaned = cleaned.replace(/\\/g, "_");
    cleaned = cleaned.replace(/@import\b[^;{}]*(?:;|$)/gi, "");
    cleaned = cleaned.replace(SVG_STYLE_HAZARD_FUNC_RE, "none");
    cleaned = cleaned.replace(SVG_STYLE_NON_FRAG_URL_RE, "none");
    cleaned = cleaned.replace(/(?:behavior|-moz-binding)\s*:[^;{}]*(?:;|$)/gi, "");
    cleaned = cleaned.replace(/(?:javascript|vbscript)\s*:/gi, "");
    return cleaned;
  }

  function sanitizeMermaidSvg(svgMarkup) {
    const tpl = document.createElement("template");
    tpl.innerHTML = svgMarkup;
    const forbidden = Array.from(tpl.content.querySelectorAll(FORBIDDEN_MERMAID_TAGS));
    for (let i = 0; i < forbidden.length; i++) {
      forbidden[i].remove();
    }

    const elements = Array.from(tpl.content.querySelectorAll("*"));
    for (let i = 0; i < elements.length; i++) {
      const node = elements[i];
      const tag = (node.tagName || "").toLowerCase();
      const attrs = Array.from(node.attributes || []);

      for (let j = 0; j < attrs.length; j++) {
        const attr = attrs[j];
        const name = (attr.name || "").toLowerCase();
        const val = attr.value || "";

        if (name.startsWith("on") || name === "srcdoc" || name === "srcset") {
          node.removeAttribute(attr.name);
          continue;
        }

        const isLinkAttr = name === "href" || name === "xlink:href" || attr.localName === "href";
        if (tag === "img" || tag === "image") {
          if (name === "src" || isLinkAttr) {
            if (!isSafeRasterDataUri(val)) {
              node.removeAttribute(attr.name);
            }
          }
          continue;
        }

        if (tag === "use") {
          if (isLinkAttr && !val.trim().startsWith("#")) {
            node.removeAttribute(attr.name);
          }
          continue;
        }

        if (isLinkAttr) {
          if (!isSafeMermaidLinkUri(val)) {
            node.removeAttribute(attr.name);
          }
          continue;
        }

        if (name === "style") {
          const safeStyle = sanitizeInlineStyleValue(val);
          if (safeStyle) {
            node.setAttribute(attr.name, safeStyle);
          } else {
            node.removeAttribute(attr.name);
          }
        }
      }

      if (tag === "style") {
        node.textContent = sanitizeSvgStyleText(node.textContent || "");
      }
    }

    return tpl.content;
  }

  function cleanupMermaidTempNodes(svgId, ownerEl) {
    const tempDiv = document.getElementById("d" + svgId);
    if (tempDiv && !ownerEl.contains(tempDiv)) {
      tempDiv.remove();
    }
    const tempIframe = document.getElementById("i" + svgId);
    if (tempIframe && !ownerEl.contains(tempIframe)) {
      tempIframe.remove();
    }
    const orphanSvg = document.getElementById(svgId);
    if (orphanSvg && !ownerEl.contains(orphanSvg)) {
      orphanSvg.remove();
    }
  }

  async function renderAllMermaid() {
    const mermaidErrors = [];
    const nodes = Array.from(document.querySelectorAll(".mermaid-block"));
    if (nodes.length === 0) {
      return mermaidErrors;
    }

    const hasMermaid =
      typeof window.mermaid === "object" &&
      window.mermaid !== null &&
      typeof window.mermaid.render === "function";

    if (hasMermaid && !mermaidInitialized && typeof window.mermaid.initialize === "function") {
      window.mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        suppressErrorRendering: true,
      });
      mermaidInitialized = true;
    }

    const usedSvgIds = new Set();

    for (let seqIdx = 0; seqIdx < nodes.length; seqIdx++) {
      const el = nodes[seqIdx];
      if (el.dataset && el.dataset.mdtopdfRendered === "1") {
        continue;
      }

      const index = parseIndex(el.dataset ? el.dataset.mermaidIndex : undefined, seqIdx);
      const src =
        el.dataset && typeof el.dataset.mermaidSrc === "string"
          ? el.dataset.mermaidSrc
          : el.textContent || "";
      const snippet = makeSnippet(src);

      if (!hasMermaid) {
        const msg = "Mermaid library is not loaded";
        renderMermaidError(el, msg);
        if (el.dataset) {
          el.dataset.mdtopdfRendered = "1";
        }
        mermaidErrors.push({ index, message: msg, snippet });
        continue;
      }

      let svgId = "mermaid-svg-" + index;
      if (usedSvgIds.has(svgId) || document.getElementById(svgId)) {
        svgId = "mermaid-svg-" + index + "-" + seqIdx;
      }
      usedSvgIds.add(svgId);

      try {
        const rendered = await window.mermaid.render(svgId, src);
        const svgMarkup = rendered && typeof rendered.svg === "string" ? rendered.svg : "";
        el.textContent = "";
        el.classList.add("mermaid-container");
        // Sanitize Mermaid's rendered SVG/HTML output via inert <template> before DOM insertion.
        el.appendChild(sanitizeMermaidSvg(svgMarkup));

        const svgEl = el.querySelector("svg");
        if (!svgEl) {
          throw new Error("Mermaid render produced no SVG element");
        }
        if (svgEl.style) {
          svgEl.style.removeProperty("max-width");
        }
      } catch (err) {
        const msg = formatErrorMessage(err);
        renderMermaidError(el, msg);
        mermaidErrors.push({ index, message: msg, snippet });
      } finally {
        cleanupMermaidTempNodes(svgId, el);
      }

      if (el.dataset) {
        el.dataset.mdtopdfRendered = "1";
      }
    }

    return mermaidErrors;
  }

  function resolvePrintableWidthPx(explicitWidth) {
    if (typeof explicitWidth === "number" && Number.isFinite(explicitWidth) && explicitWidth > 0) {
      return explicitWidth;
    }
    const docWidth = document.documentElement ? document.documentElement.clientWidth : 0;
    if (typeof docWidth === "number" && Number.isFinite(docWidth) && docWidth > 0) {
      return docWidth;
    }
    if (typeof window.innerWidth === "number" && Number.isFinite(window.innerWidth) && window.innerWidth > 0) {
      return window.innerWidth;
    }
    return 0;
  }

  function countTableColumns(table) {
    let maxCols = 0;
    const rows = table.rows;
    if (!rows) {
      return 0;
    }
    for (let r = 0; r < rows.length; r++) {
      let rowCols = 0;
      const cells = rows[r].cells;
      if (cells) {
        for (let c = 0; c < cells.length; c++) {
          rowCols += cells[c].colSpan || 1;
        }
      }
      if (rowCols > maxCols) {
        maxCols = rowCols;
      }
    }
    return maxCols;
  }

  function isElementVisuallyScaled(el) {
    if (typeof window.getComputedStyle !== "function") {
      return false;
    }
    const cs = window.getComputedStyle(el);
    if (!cs) {
      return false;
    }
    if (cs.transform && cs.transform !== "none") {
      return true;
    }
    if (cs.zoom) {
      const z = Number.parseFloat(cs.zoom);
      if (Number.isFinite(z) && z > 0 && z < 0.999) {
        return true;
      }
    }
    return false;
  }

  function inspectGeometry(printableWidthPx) {
    const effectiveWidth = resolvePrintableWidthPx(printableWidthPx);
    const tableOverflows = [];
    const tables = Array.from(document.querySelectorAll("table"));

    for (let idx = 0; idx < tables.length; idx++) {
      const table = tables[idx];
      table.setAttribute("data-mdtopdf-table-idx", String(idx));

      const rect = table.getBoundingClientRect();
      const rectWidth = rect ? rect.width : 0;
      const scrollWidth = table.scrollWidth || 0;
      const parentEl = table.parentElement;
      const rawParentWidth = parentEl ? parentEl.clientWidth : 0;
      const parentWidth = rawParentWidth > 0 ? rawParentWidth : effectiveWidth;

      const scaled = isElementVisuallyScaled(table);
      const effectiveLayoutWidth = scaled ? rectWidth : scrollWidth;

      const overflowsPrintable = rectWidth > effectiveWidth + OVERFLOW_TOLERANCE_PX;
      const overflowsParent = effectiveLayoutWidth > parentWidth + OVERFLOW_TOLERANCE_PX;

      if (overflowsPrintable || overflowsParent) {
        const overflowPx = Math.max(
          rectWidth - effectiveWidth,
          effectiveLayoutWidth - parentWidth,
          0
        );
        tableOverflows.push({
          table_index: idx,
          rect_width: rectWidth,
          scroll_width: scrollWidth,
          parent_client_width: parentWidth,
          printable_width_px: effectiveWidth,
          overflow_px: overflowPx,
          column_count: countTableColumns(table),
        });
      }
    }

    const wideElements = [];
    const candidates = Array.from(document.querySelectorAll("img, pre, svg, .katex-display"));
    for (let i = 0; i < candidates.length; i++) {
      const el = candidates[i];
      const tagName = el.tagName ? el.tagName.toLowerCase() : "";

      if (tagName === "svg") {
        if (el.closest(".katex")) {
          continue;
        }
        if (el.parentElement && el.parentElement.closest("svg")) {
          continue;
        }
      }

      const rect = el.getBoundingClientRect();
      const rectWidth = rect ? rect.width : 0;
      const isKatexDisplay = Boolean(el.classList && el.classList.contains("katex-display"));
      let measuredWidth = rectWidth;
      if (!isElementVisuallyScaled(el)) {
        if (tagName === "pre" || isKatexDisplay) {
          measuredWidth = Math.max(measuredWidth, el.scrollWidth || 0);
        }
        if (isKatexDisplay) {
          const innerNodes = el.querySelectorAll(".katex, .katex-html");
          for (let k = 0; k < innerNodes.length; k++) {
            const innerEl = innerNodes[k];
            const innerRect = innerEl.getBoundingClientRect();
            const innerRectWidth = innerRect ? innerRect.width : 0;
            const innerScrollWidth = innerEl.scrollWidth || 0;
            measuredWidth = Math.max(measuredWidth, innerRectWidth, innerScrollWidth);
          }
        }
      }
      if (measuredWidth > effectiveWidth + OVERFLOW_TOLERANCE_PX) {
        const tag = isKatexDisplay ? "katex-display" : tagName;
        wideElements.push({
          tag,
          rect_width: measuredWidth,
          printable_width_px: effectiveWidth,
          overflow_px: Math.max(measuredWidth - effectiveWidth, 0),
          in_table: Boolean(el.closest("table")),
          in_mermaid: Boolean(el.closest(".mermaid-container")),
        });
      }
    }

    return {
      printable_width_px: effectiveWidth,
      table_overflows: tableOverflows,
      wide_elements: wideElements,
    };
  }

  async function renderAndInspect(printableWidthPx) {
    await waitForFonts();
    await waitForImages();

    const mathErrors = renderAllMath();
    const mermaidErrors = await renderAllMermaid();

    await waitForFonts();
    await waitForImages();

    const geometry = inspectGeometry(printableWidthPx);
    return {
      mermaid_errors: mermaidErrors,
      math_errors: mathErrors,
      table_overflows: geometry.table_overflows,
      wide_elements: geometry.wide_elements,
      printable_width_px: geometry.printable_width_px,
    };
  }

  window.__mdtopdfInspectGeometry = inspectGeometry;
  window.__mdtopdfRenderAndInspect = renderAndInspect;
})();
