// Mermaid integration. The library is vendored (vendor/mermaid.min.js), loaded lazily, and run with
// securityLevel "strict". Any failure falls back to showing the diagram source; nothing here throws.
import { el, button } from "../dom.js";
import { effectiveTheme, on } from "../state.js";

let loading = null;
const sources = new WeakMap();
let counter = 0;
let queue = Promise.resolve();

function loadLibrary() {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "vendor/mermaid.min.js";
      script.onload = () => (window.mermaid ? resolve(window.mermaid) : reject(new Error("Mermaid did not initialise")));
      script.onerror = () => {
        loading = null;
        reject(new Error("The bundled Mermaid library could not be loaded"));
      };
      document.head.appendChild(script);
    });
  }
  return loading;
}

// mermaid.render is not safe to run concurrently, so renders are chained.
function enqueue(task) {
  const run = queue.then(task, task);
  queue = run.catch(() => {});
  return run;
}

async function renderSvg(source) {
  const mermaid = await loadLibrary();
  return enqueue(async () => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: effectiveTheme() === "dark" ? "dark" : "default",
      suppressErrorRendering: true,
      fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    });
    const id = `mmd-${++counter}`;
    try {
      const { svg } = await mermaid.render(id, source);
      return svg;
    } finally {
      document.getElementById(`d${id}`)?.remove(); // mermaid can leave its scratch node behind on errors
    }
  });
}

// Mermaid's output is already sanitised in strict mode; parsing it into nodes (never innerHTML) and
// stripping scripts/handlers is a second, independent layer.
function toSvgElement(svgText) {
  const doc = new DOMParser().parseFromString(svgText, "text/html");
  const svg = doc.body.querySelector("svg");
  if (!svg) throw new Error("Mermaid returned no SVG");
  svg.querySelectorAll("script, iframe, object, embed, link, meta, base").forEach((n) => n.remove());
  for (const node of [svg, ...svg.querySelectorAll("*")]) {
    for (const attr of [...node.attributes]) {
      const name = attr.name.toLowerCase();
      const unsafeUrl = (name === "href" || name === "xlink:href") && /^\s*(javascript|data):/i.test(attr.value);
      if (name.startsWith("on") || unsafeUrl) node.removeAttribute(attr.name);
    }
  }
  return document.importNode(svg, true);
}

function fallback(box, source, message) {
  box.classList.add("mermaid-failed");
  box.replaceChildren(
    el("p", { class: "note", role: "note", text: `Diagram could not be rendered (${message}). Showing the Mermaid source instead.` }),
    el("pre", {}, el("code", { text: source })),
  );
}

async function draw(box) {
  const source = sources.get(box);
  box.classList.remove("mermaid-failed");
  box.replaceChildren(el("p", { class: "mermaid-status", text: "Rendering diagram\u2026" }));
  try {
    const svg = toSvgElement(await renderSvg(source));
    svg.removeAttribute("height");
    const viewBox = (svg.getAttribute("viewBox") || "").split(/\s+/).map(Number);
    const actualWidth = viewBox.length === 4 && viewBox[2] > 0 ? Math.round(viewBox[2]) : null;
    const canvas = el("div", { class: "mermaid-canvas" }, svg);
    let actual = false;
    const sizeButton = button("Actual size", {
      variant: "ghost", small: true,
      onclick: () => {
        actual = !actual;
        svg.style.maxWidth = actual ? "none" : "";
        svg.style.width = actual && actualWidth ? `${actualWidth}px` : "";
        sizeButton.querySelector("span").textContent = actual ? "Fit to width" : "Actual size";
      },
    });
    box.replaceChildren(
      canvas,
      el("div", { class: "mermaid-tools" }, actualWidth ? sizeButton : null,
        el("details", { class: "mermaid-source" }, el("summary", { text: "Diagram source" }), el("pre", {}, el("code", { text: source })))),
    );
  } catch (err) {
    fallback(box, source, err && err.message ? err.message : "unknown error");
  }
}

export function mermaidBlock(source) {
  const box = el("figure", { class: "mermaid-block", role: "group", "aria-label": "Diagram" });
  sources.set(box, source);
  draw(box);
  return box;
}

// Diagrams pick their palette at render time, so redraw the visible ones when the theme flips.
on("theme", () => {
  document.querySelectorAll(".mermaid-block").forEach((box) => {
    if (sources.has(box)) draw(box);
  });
});
