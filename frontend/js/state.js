// Global application state with tiny pub/sub and localStorage persistence for user selections.

const KEYS = { repoId: "st.repoId", featureId: "st.featureId", depth: "st.depth", theme: "st.theme" };
export const DEPTHS = ["beginner", "intermediate", "expert"]; // literal values accepted by LearnRequest.depth

function read(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function write(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, String(value));
  } catch { /* storage may be blocked; selections then last for this page load only */ }
}
const readInt = (key) => {
  const n = parseInt(read(key), 10);
  return Number.isFinite(n) ? n : null;
};

const listeners = new Map();

export const state = {
  repos: [],
  features: [],
  health: null, // {status, version, llm:{provider, model, mock}, embeddings}
  repoId: readInt(KEYS.repoId),
  featureId: readInt(KEYS.featureId),
  depth: DEPTHS.includes(read(KEYS.depth)) ? read(KEYS.depth) : "intermediate",
  indexStatus: {}, // repoId -> latest index status from the server
  handoff: null, // one-shot message from one view to the next (e.g. deep dive request)
};

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

export function emit(event, payload) {
  for (const fn of [...(listeners.get(event) || [])]) {
    try { fn(payload); } catch (err) { console.error(`listener for "${event}" failed`, err); }
  }
}

export const currentRepo = () => state.repos.find((r) => r.id === state.repoId) || null;
export const currentFeature = () => state.features.find((f) => f.id === state.featureId) || null;
export const isIndexed = (repo) => !!repo && (repo.chunk_count > 0 || !!repo.last_indexed_at);

export function setRepos(repos) {
  state.repos = repos;
  if (!repos.some((r) => r.id === state.repoId)) setRepo(repos.length ? repos[0].id : null);
  emit("repos", repos);
}

export function setRepo(id) {
  if (state.repoId === id) return;
  state.repoId = id;
  state.featureId = null;
  state.features = [];
  write(KEYS.repoId, id);
  write(KEYS.featureId, null);
  emit("repo", id);
}

export function setFeatures(features) {
  state.features = features;
  if (state.featureId && !features.some((f) => f.id === state.featureId)) {
    state.featureId = null;
    write(KEYS.featureId, null);
  }
  emit("features", features);
}

export function setFeature(id) {
  state.featureId = id || null;
  write(KEYS.featureId, state.featureId);
  emit("feature", state.featureId);
}

export function setDepth(depth) {
  if (!DEPTHS.includes(depth)) return;
  state.depth = depth;
  write(KEYS.depth, depth);
  emit("depth", depth);
}

export function setHealth(health) {
  state.health = health;
  emit("health", health);
}

// ---------------------------------------------------------------- theme
export function systemTheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function effectiveTheme() {
  const saved = document.documentElement.dataset.theme;
  return saved === "dark" || saved === "light" ? saved : systemTheme();
}

export function toggleTheme() {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  write(KEYS.theme, next);
  emit("theme", next);
}

// One-shot handoff between views, consumed by the receiving view on mount.
export function setHandoff(payload) {
  state.handoff = payload;
}
export function takeHandoff(kind) {
  const h = state.handoff;
  if (h && h.kind === kind) {
    state.handoff = null;
    return h;
  }
  return null;
}
