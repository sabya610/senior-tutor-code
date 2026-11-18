import { el } from "../dom.js";
import { mountChat } from "../components/chatview.js";

export function mount(root) {
  return mountChat(root, {
    route: "compare",
    title: "Compare & Connect",
    blurb: "Understand how two concepts differ and how they connect, for example a controller and a webhook, or a service and its client.",
    panel: {
      mode: "compare",
      placeholder: "First concept, for example: Validating webhook",
      submitLabel: "Compare",
      compare: true,
      rows: 2,
      examples: ["Reconciler", "Service layer", "Middleware"],
      intro: el("div", { class: "stack" },
        el("h3", { text: "See the relationship, not just the parts" }),
        el("p", { class: "muted", text: "Enter two concepts from this codebase. The tutor retrieves code for each one and explains differences, shared dependencies and how they interact." })),
    },
  });
}
