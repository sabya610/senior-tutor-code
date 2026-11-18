// Small DOM helpers. Everything here builds nodes with textContent/createElement, so untrusted
// strings are never parsed as HTML.

export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "for") node.htmlFor = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (key.includes("-") || key === "role" || key === "list") node.setAttribute(key, value === true ? "" : value);
      else if (key in node) node[key] = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
  }
  append(node, children);
  return node;
}

export function append(parent, children) {
  for (const child of children) {
    if (child == null || child === false) continue;
    if (Array.isArray(child)) append(parent, child);
    else if (child instanceof Node) parent.appendChild(child);
    else parent.appendChild(document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

export function $(selector, root = document) {
  return root.querySelector(selector);
}

// ---------------------------------------------------------------- icons (24px line icons, drawn here, no external assets)
const ICONS = {
  dashboard: "M3 3h8v8H3z M13 3h8v5h-8z M13 10h8v11h-8z M3 13h8v8H3z",
  repos: "M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  explorer: "M4 5h5l2 2h9v12H4z M4 11h16",
  architecture: "M12 3l9 5-9 5-9-5z M3 13l9 5 9-5 M3 17.5l9 5 9-5",
  features: "M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M16.5 13v7 M13 16.5h7",
  explain: "M8 7l-5 5 5 5 M16 7l5 5-5 5 M14 4l-4 16",
  flow: "M4 5h6v4H4z M14 15h6v4h-6z M10 7h4a3 3 0 0 1 3 3v5",
  prep: "M5 3h14v18H5z M9 8h6 M9 12h6 M9 16h4",
  troubleshoot: "M12 3l10 18H2z M12 10v5 M12 18h.01",
  history: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 7v5l3 2",
  knowledge: "M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z M5 17a3 3 0 0 1 3-3h11",
  settings: "M4 7h9 M17 7h3 M13 5v4 M4 17h3 M11 17h9 M7 15v4 M4 12h5 M13 12h7 M9 10v4",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  close: "M6 6l12 12 M18 6L6 18",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M12 2v2 M12 20v2 M4.9 4.9l1.4 1.4 M17.7 17.7l1.4 1.4 M2 12h2 M20 12h2 M4.9 19.1l1.4-1.4 M17.7 6.3l1.4-1.4",
  moon: "M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z",
  copy: "M9 9h11v11H9z M5 15H4V4h11v1",
  bookmark: "M6 3h12v18l-6-4-6 4z",
  refresh: "M20 11a8 8 0 0 0-14-4L4 9 M4 4v5h5 M4 13a8 8 0 0 0 14 4l2-2 M20 20v-5h-5",
  trash: "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13 M10 11v6 M14 11v6",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M20 20l-4-4",
  send: "M3 11l18-8-8 18-2-8z",
  download: "M12 3v12 M7 11l5 5 5-5 M4 20h16",
  file: "M6 3h8l5 5v13H6z M14 3v5h5",
  chevron: "M9 6l6 6-6 6",
  quiz: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7 M12 17h.01",
  plus: "M12 5v14 M5 12h14",
  compare: "M7 4v16 M17 4v16 M3 8l4-4 4 4 M13 16l4 4 4-4",
  check: "M5 12l5 5 9-10",
  alert: "M12 3l10 18H2z M12 10v5 M12 18h.01",
};

export function icon(name, size = 18) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name] || ICONS.file);
  svg.appendChild(path);
  return svg;
}

export function button(label, { variant = "secondary", iconName, onclick, title, type = "button", disabled, small, ariaLabel, ...rest } = {}) {
  return el("button", {
    type,
    class: `btn btn-${variant}${small ? " btn-sm" : ""}`,
    onclick,
    title,
    disabled,
    "aria-label": ariaLabel,
    ...rest,
  }, iconName ? icon(iconName, small ? 15 : 17) : null, label ? el("span", { text: label }) : null);
}

export function iconButton(iconName, label, onclick, extra = {}) {
  return el("button", { type: "button", class: "icon-btn", "aria-label": label, title: label, onclick, ...extra }, icon(iconName));
}

// ---------------------------------------------------------------- formatting
export const fmtNum = (n) => (n == null ? "-" : Number(n).toLocaleString());

// SQLite timestamps are "YYYY-MM-DD HH:MM:SS" in UTC with no zone marker.
export function parseDate(value) {
  if (!value) return null;
  const iso = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value) ? value.replace(" ", "T") + "Z" : value;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(value) {
  const d = parseDate(value);
  return d ? d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "never";
}

export function timeAgo(value) {
  const d = parseDate(value);
  if (!d) return "never";
  const seconds = Math.round((Date.now() - d.getTime()) / 1000);
  if (seconds < 45) return "just now";
  const steps = [[60, "minute"], [3600, "hour"], [86400, "day"], [2592000, "month"]];
  let unit = ["second", 1];
  for (const [limit, name] of steps) if (seconds >= limit) unit = [name, limit];
  const n = Math.max(1, Math.round(seconds / unit[1]));
  if (unit[0] === "month" && n > 11) return fmtDate(value);
  return `${n} ${unit[0]}${n === 1 ? "" : "s"} ago`;
}

export function slug(text, fallback = "document") {
  const s = String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
  return s || fallback;
}

export const truncate = (text, max) => (text.length > max ? text.slice(0, max - 1) + "\u2026" : text);

// ---------------------------------------------------------------- clipboard / download
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = el("textarea", { value: text, "aria-hidden": "true", class: "offscreen" });
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (!ok) throw new Error("Clipboard is not available in this browser context");
  }
}

export function downloadText(filename, text, mime = "text/markdown") {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const link = el("a", { href: url, download: filename, class: "offscreen" });
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------------------------------------------------------------- toasts and confirm dialog
export function toast(message, kind = "info", timeout = 4500) {
  const host = document.getElementById("toasts");
  const node = el("div", { class: `toast toast-${kind}` }, el("span", { text: message }));
  host.appendChild(node);
  setTimeout(() => node.remove(), kind === "error" ? Math.max(timeout, 7000) : timeout);
}

export function confirmDialog({ title, message, confirmText = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const opener = document.activeElement;
    const dialog = el("dialog", { class: "confirm", "aria-labelledby": "confirm-title" });
    const finish = (value) => {
      dialog.close();
      dialog.remove();
      if (opener && opener.focus) opener.focus();
      resolve(value);
    };
    dialog.append(
      el("h2", { id: "confirm-title", text: title }),
      el("p", { text: message }),
      el("div", { class: "row end" },
        button("Cancel", { variant: "secondary", onclick: () => finish(false) }),
        button(confirmText, { variant: danger ? "danger" : "primary", onclick: () => finish(true) })),
    );
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(false); });
    document.body.appendChild(dialog);
    dialog.showModal();
    dialog.querySelector("button").focus();
  });
}

export function debounce(fn, ms = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function badge(text, kind = "", title) {
  return el("span", { class: `badge${kind ? " badge-" + kind : ""}`, title }, text);
}

export function emptyState(title, body, ...actions) {
  return el("div", { class: "empty" },
    el("h3", { text: title }),
    body ? el("p", { text: body }) : null,
    actions.length ? el("div", { class: "row center" }, actions) : null);
}

export function spinner(label = "Loading") {
  return el("div", { class: "loading", role: "status" }, el("span", { class: "spinner", "aria-hidden": "true" }), el("span", { text: label }));
}

export function errorBox(message, ...actions) {
  return el("div", { class: "alert alert-error", role: "alert" }, el("div", { text: message }), actions.length ? el("div", { class: "row" }, actions) : null);
}

export function labelled(labelText, control, hint) {
  const id = control.id || `f${Math.random().toString(36).slice(2, 9)}`;
  control.id = id;
  return el("div", { class: "field" },
    el("label", { for: id, text: labelText }),
    control,
    hint ? el("p", { class: "hint", text: hint }) : null);
}
