import { el } from "../dom.js";
import { mountChat } from "../components/chatview.js";

export function mount(root) {
  return mountChat(root, {
    route: "troubleshoot",
    title: "Troubleshooting",
    blurb: "Paste an error, log excerpt or symptom. The tutor finds the code that can produce it and proposes ranked hypotheses with the checks to run.",
    showFeature: true,
    panel: {
      mode: "troubleshoot",
      placeholder: "Paste the error message, stack trace or log lines here, and describe what you expected to happen.",
      submitLabel: "Investigate",
      rows: 9,
      minChars: 5,
      examples: [],
      intro: el("div", { class: "stack" },
        el("h3", { text: "Investigate like a senior support engineer" }),
        el("p", { class: "muted", text: "The answer lists what is known, ranked hypotheses with evidence from the code, the commands or checks to run next, and what each result would tell you. Nothing is changed in your repository." })),
    },
  });
}
