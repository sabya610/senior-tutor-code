import { el } from "../dom.js";
import { mountChat } from "../components/chatview.js";

export function mount(root) {
  return mountChat(root, {
    route: "explain",
    title: "Explain Code",
    blurb: "Ask about any file, function, class or concept. Answers cite the exact files and lines they came from.",
    showFeature: true,
    panel: {
      mode: "explain",
      placeholder: "For example: How does the reconciler decide when to requeue? Or paste a function and ask what it does.",
      submitLabel: "Explain",
      rows: 4,
      examples: [
        "Give me an overview of how this project is organised",
        "Where is configuration loaded and validated?",
        "How is error handling done across the codebase?",
        "What happens when the application starts?",
      ],
      intro: el("div", { class: "stack" },
        el("h3", { text: "Learn it like a senior engineer would teach it" }),
        el("p", { class: "muted", text: "Each answer follows the same shape: purpose, how it works, key files, and where to look next. Use the depth selector in the header to go from beginner to expert." })),
    },
  });
}
