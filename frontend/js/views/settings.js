import { api } from "../api.js";
import { badge, button, el, errorBox, labelled, spinner, toast } from "../dom.js";
import { viewHead } from "../components/layout.js";
import { setHealth } from "../state.js";

const LLM_PROVIDERS = [["auto", "Auto (OpenAI if a key is set, else Ollama, else evidence-only)"], ["openai", "OpenAI-compatible"], ["ollama", "Ollama (local)"], ["mock", "Evidence-only (no LLM)"]];
const EMBED_PROVIDERS = [["auto", "Auto"], ["openai", "OpenAI-compatible"], ["ollama", "Ollama (local)"], ["local", "Built-in hashing (offline, lower quality)"]];

function select(options, value) {
  return el("select", {}, options.map(([v, t]) => el("option", { value: v, text: t, selected: v === value })));
}

export async function mount(root) {
  root.append(viewHead("Settings", "Choose the language model and embeddings that power explanations. Without a model the tutor still works in evidence-only mode."), spinner());
  let alive = true;
  let data;
  try {
    data = await api.settings();
  } catch (err) {
    root.replaceChildren(viewHead("Settings"), errorBox(err.friendly || err.message));
    return undefined;
  }

  const cfg = data.config;
  const llmProvider = select(LLM_PROVIDERS, cfg.llm_provider);
  const llmModel = el("input", { type: "text", value: cfg.llm_model || "", list: "ollama-models", autocomplete: "off", placeholder: "Leave empty for the provider default" });
  const embedProvider = select(EMBED_PROVIDERS, cfg.embed_provider);
  const embedModel = el("input", { type: "text", value: cfg.embed_model || "", list: "ollama-models", autocomplete: "off", placeholder: "Leave empty for the provider default" });
  const ollamaUrl = el("input", { type: "url", value: cfg.ollama_base_url || "", autocomplete: "off", placeholder: "http://localhost:11434" });
  const openaiUrl = el("input", { type: "url", value: cfg.openai_base_url || "", autocomplete: "off", placeholder: "https://api.openai.com/v1" });
  const status = el("div", { class: "stack" });
  const testOut = el("div", { class: "stack" });
  const models = el("datalist", { id: "ollama-models" }, (data.ollama.models || []).map((m) => el("option", { value: m })));

  function paintStatus(view) {
    const ol = view.ollama;
    const llm = view.active.llm;
    status.replaceChildren(
      el("div", { class: "row" },
        el("strong", { text: "Active:" }),
        badge(llm.mock ? "LLM: evidence-only" : `LLM: ${llm.provider} \u00b7 ${llm.model}`, llm.mock ? "warn" : "ok"),
        badge(`Embeddings: ${view.active.embeddings}`, "info")),
      el("div", { class: "row" },
        badge(ol.reachable ? "Ollama reachable" : "Ollama not reachable", ol.reachable ? "ok" : "warn"),
        ol.reachable ? el("span", { class: "small muted", text: ol.models.length ? `Models: ${ol.models.join(", ")}` : "No models pulled yet" }) : null,
        badge(view.openai_key_configured ? "OPENAI_API_KEY is set" : "OPENAI_API_KEY not set", view.openai_key_configured ? "ok" : ""),
      ));
  }
  paintStatus(data);

  const save = button("Save settings", { variant: "primary", type: "submit", iconName: "check" });
  const form = el("form", { class: "card stack", onsubmit: async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      const view = await api.saveSettings({
        llm_provider: llmProvider.value, llm_model: llmModel.value.trim(), embed_provider: embedProvider.value, embed_model: embedModel.value.trim(),
        ollama_base_url: ollamaUrl.value.trim(), openai_base_url: openaiUrl.value.trim(),
      });
      if (!alive) return;
      paintStatus(view);
      setHealth(await api.health());
      toast(view.reindex_recommended ? "Saved. Embeddings changed, so re-index your repositories for best results." : "Settings saved", view.reindex_recommended ? "info" : "success", 7000);
    } catch (err) {
      toast(err.friendly || err.message, "error");
    } finally {
      save.disabled = false;
    }
  } },
    el("h2", { text: "Language model" }),
    labelled("Provider", llmProvider),
    labelled("Model", llmModel, "For example llama3.2 for Ollama, or gpt-4o-mini for OpenAI."),
    el("h2", { text: "Embeddings (semantic search)" }),
    labelled("Provider", embedProvider, "Changing the embedding provider or model means repositories should be re-indexed."),
    labelled("Model", embedModel),
    el("h2", { text: "Endpoints" }),
    labelled("Ollama base URL", ollamaUrl, "From inside Docker, use http://host.docker.internal:11434 to reach Ollama on the host."),
    labelled("OpenAI-compatible base URL", openaiUrl),
    models,
    el("div", { class: "row" }, save));

  const testButton = button("Test connection", { iconName: "refresh", onclick: async () => {
    testButton.disabled = true;
    testOut.replaceChildren(spinner("Contacting the model\u2026"));
    try {
      const r = await api.testSettings();
      if (!alive) return;
      testOut.replaceChildren(r.ok
        ? el("div", { class: "alert alert-ok", role: "status", text: `Connected to ${r.provider.provider} \u00b7 ${r.provider.model} in ${r.latency_ms} ms. Reply: ${r.reply}` })
        : errorBox(`Connection failed: ${r.error}`));
    } catch (err) {
      if (alive) testOut.replaceChildren(errorBox(err.friendly || err.message));
    } finally {
      testButton.disabled = false;
    }
  } });

  root.replaceChildren(viewHead("Settings", "Choose the language model and embeddings that power explanations. Without a model the tutor still works in evidence-only mode."),
    el("div", { class: "grid cols-2" },
      form,
      el("div", { class: "stack" },
        el("section", { class: "card stack" }, el("h2", { text: "Status" }), status, el("div", { class: "row" }, testButton), testOut),
        el("section", { class: "card stack" }, el("h2", { text: "Secrets and access" }),
          el("p", { class: "small", text: "API keys are never entered or stored in the browser. Set OPENAI_API_KEY as an environment variable on the server (or in the container) and restart." }),
          el("p", { class: "small muted", text: "Repositories can only be added from these allowed locations:" }),
          el("ul", {}, (data.allowed_roots || []).map((p) => el("li", { class: "mono small", text: p })))))));
  return () => { alive = false; };
}
