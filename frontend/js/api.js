// Thin fetch wrapper around the FastAPI backend. Same-origin only (the CSP enforces connect-src 'self').

export class ApiError extends Error {
  constructor(status, detail, friendly) {
    super(friendly);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.friendly = friendly;
  }
  get notIndexed() {
    return /not been indexed/i.test(this.detail || "");
  }
}

function detailText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) { // FastAPI request-validation errors
    return detail.map((d) => `${(d.loc || []).filter((p) => p !== "body").join(".")}: ${d.msg}`).join("; ");
  }
  return detail ? JSON.stringify(detail) : "";
}

function friendlyMessage(status, detail) {
  const d = detailText(detail);
  switch (status) {
    case 400: return d || "The request was not valid.";
    case 403: return d ? `Not allowed: ${d}` : "This action is not allowed.";
    case 404: return d ? `Not found: ${d}` : "The requested item was not found. It may have been deleted.";
    case 409: return d || "The server is not ready for this action yet.";
    case 413: return d || "That file is too large to display.";
    case 422: return `Please check the input (${d}).`;
    case 502: return `The language model could not complete the request. ${d.replace(/^LLM error:\s*/i, "")} Check the provider in Settings and try again.`.trim();
    default:
      return status >= 500 ? `The server hit an error (${status}). ${d}`.trim() : d || `Request failed (${status}).`;
  }
}

export async function request(method, path, { query, body, signal } = {}) {
  let url = `/api${path}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  const init = { method, signal, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, String(err), "Cannot reach the tutor server. Is it still running?");
  }
  if (response.status === 204) return null;
  let data = null;
  const text = await response.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (!response.ok) {
    const detail = data && data.detail !== undefined ? data.detail : text.slice(0, 200);
    throw new ApiError(response.status, detailText(detail), friendlyMessage(response.status, detail));
  }
  return data;
}

const get = (path, query, signal) => request("GET", path, { query, signal });
const post = (path, body, opts = {}) => request("POST", path, { body: body ?? {}, ...opts });

export const api = {
  health: () => get("/health"),
  dashboard: () => get("/dashboard"),

  repos: () => get("/repos"),
  addRepo: (path, name) => post("/repos", { path, name: name || null }),
  discover: (root) => get("/repos/discover", { root }),
  deleteRepo: (id) => request("DELETE", `/repos/${id}`),
  startIndex: (id, full = false) => post(`/repos/${id}/index`, { full }),
  indexStatus: (id) => get(`/repos/${id}/index/status`),
  files: (id) => get(`/repos/${id}/files`),
  file: (id, path, start, end, signal) => get(`/repos/${id}/file`, { path, start, end }, signal),
  symbols: (id, q, kind, limit) => get(`/repos/${id}/symbols`, { q, kind, limit }),
  search: (id, q, k) => get(`/repos/${id}/search`, { q, k }),
  architecture: (id) => get(`/repos/${id}/architecture`),
  narrative: (id, force) => request("POST", `/repos/${id}/architecture/narrative`, { query: { force: force ? "true" : undefined } }),
  features: (id) => get(`/repos/${id}/features`),
  feature: (id) => get(`/features/${id}`),

  learn: (body, signal) => post("/learn", body, { signal }),
  quiz: (body) => post("/quiz", body),
  evaluate: (body) => post("/quiz/evaluate", body),
  quizResults: (repoId) => get("/quiz/results", { repo_id: repoId }),
  sessionPrep: (body) => post("/session-prep", body),

  sessions: (repoId) => get("/sessions", { repo_id: repoId }),
  session: (id) => get(`/sessions/${id}`),
  deleteSession: (id) => request("DELETE", `/sessions/${id}`),

  bookmarks: (repoId) => get("/bookmarks", { repo_id: repoId }),
  addBookmark: (body) => post("/bookmarks", body),
  deleteBookmark: (id) => request("DELETE", `/bookmarks/${id}`),

  knowledge: (repoId, mode) => get("/knowledge", { repo_id: repoId, mode }),
  knowledgeItem: (id) => get(`/knowledge/${id}`),
  deleteKnowledge: (id) => request("DELETE", `/knowledge/${id}`),

  settings: () => get("/settings"),
  saveSettings: (body) => request("PUT", "/settings", { body }),
  testSettings: () => post("/settings/test"),
};
