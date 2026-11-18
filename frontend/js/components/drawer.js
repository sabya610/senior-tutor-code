// Slide-over source viewer used by every clickable path:line reference.
import { api } from "../api.js";
import { $, button, clear, copyText, el, icon, toast } from "../dom.js";
import { state } from "../state.js";

const CONTEXT = 12;
const FULL_FILE_LINES = 3000;
let opener = null;
let ticket = 0;

export function initDrawer() {
  $("#drawer-close").replaceChildren(icon("close"));
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    const drawer = $("#drawer");
    if (drawer.hidden) return;
    if (event.key === "Escape" && !document.querySelector("dialog[open]")) {
      closeDrawer();
    } else if (event.key === "Tab") {
      const focusable = [...drawer.querySelectorAll("button, select, [tabindex='0']")].filter((n) => !n.disabled);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
}

export function closeDrawer() {
  ticket++;
  $("#drawer").hidden = true;
  $("#drawer-scrim").hidden = true;
  if (opener && opener.isConnected) opener.focus();
  opener = null;
}

export async function openSource(path, start = 1, end = start, repoId = state.repoId) {
  const drawer = $("#drawer");
  if (drawer.hidden) opener = document.activeElement;
  drawer.hidden = false;
  $("#drawer-scrim").hidden = false;
  $("#drawer-title").textContent = path;
  $("#drawer-sub").textContent = "Loading\u2026";
  $("#drawer-banner").hidden = true;
  clear($("#drawer-tools"));
  clear($("#drawer-body"));
  $("#drawer-close").focus();
  await load(path, start, end, repoId, Math.max(1, start - CONTEXT), end + CONTEXT);
}

async function load(path, start, end, repoId, from, to) {
  const mine = ++ticket;
  let data;
  try {
    data = await api.file(repoId, path, from, to);
  } catch (err) {
    if (mine !== ticket) return;
    $("#drawer-sub").textContent = "";
    const banner = $("#drawer-banner");
    banner.hidden = false;
    banner.textContent = err.friendly || String(err);
    return;
  }
  if (mine !== ticket) return;

  $("#drawer-sub").textContent = `Lines ${data.start}\u2013${data.end} of ${data.total_lines} \u00b7 ${data.language}`;
  const banner = $("#drawer-banner");
  banner.hidden = !data.changed_since_index;
  banner.textContent = data.changed_since_index
    ? "This file changed after it was last indexed, so line numbers may not match the explanation. Re-index the repository to refresh."
    : "";

  const body = $("#drawer-body");
  const rows = data.lines.map((text, i) => {
    const number = data.start + i;
    return el("div", { class: `code-line${number >= start && number <= end ? " hl" : ""}`, dataset: { line: number } },
      el("span", { class: "ln", text: String(number) }), el("span", { class: "src", text: text || " " }));
  });
  body.replaceChildren(...rows);
  body.querySelector(".hl")?.scrollIntoView({ block: "center" });

  const tools = $("#drawer-tools");
  clear(tools);
  tools.append(
    button("Copy path", { variant: "secondary", small: true, iconName: "copy", onclick: () => copyText(`${path}:${start}${end > start ? "-" + end : ""}`).then(() => toast("Path copied", "success")).catch((e) => toast(e.message, "error")) }),
    button(data.total_lines > data.end - data.start + 1 ? "Show whole file" : "Whole file shown", {
      variant: "secondary", small: true, iconName: "file", disabled: data.start === 1 && data.end >= data.total_lines,
      onclick: () => load(path, start, end, repoId, 1, Math.min(data.total_lines, FULL_FILE_LINES)),
    }),
  );
  if (data.total_lines > FULL_FILE_LINES && data.end >= FULL_FILE_LINES) {
    tools.append(el("span", { class: "note", text: `Showing the first ${FULL_FILE_LINES} lines.` }));
  }
  if (data.symbols.length) {
    const select = el("select", { "aria-label": "Jump to symbol" }, el("option", { value: "", text: "Jump to symbol\u2026" }),
      data.symbols.map((s) => el("option", { value: s.start_line, text: `${s.kind} ${s.name} (L${s.start_line})` })));
    select.addEventListener("change", () => {
      const line = parseInt(select.value, 10);
      if (!line) return;
      const row = body.querySelector(`[data-line="${line}"]`);
      if (row) row.scrollIntoView({ block: "start" });
      else load(path, line, line, repoId, Math.max(1, line - CONTEXT), line + 60);
    });
    tools.append(select);
  }
}
