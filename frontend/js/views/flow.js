import { el } from "../dom.js";
import { mountChat } from "../components/chatview.js";

export function mount(root) {
  return mountChat(root, {
    route: "flow",
    title: "Request Flow",
    blurb: "Trace how a request, event or command travels through the code, from entry point to side effects.",
    showFeature: true,
    panel: {
      mode: "flow",
      placeholder: "For example: What happens when a user submits the login form? Or: How is a pod create request admitted?",
      submitLabel: "Trace flow",
      rows: 3,
      examples: [
        "Trace what happens when the service receives an HTTP request",
        "Walk me through the startup sequence",
        "How does a change get from the API to storage?",
      ],
      intro: el("div", { class: "stack" },
        el("h3", { text: "Follow the data" }),
        el("p", { class: "muted", text: "You get a step-by-step trace with a sequence diagram, so you can see which component calls which, and in what order." })),
    },
  });
}
