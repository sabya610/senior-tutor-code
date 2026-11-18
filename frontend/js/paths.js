// Indexed-path lookup used to decide which `path:line` mentions in model output may become links.
import { api } from "./api.js";

let currentRepo = null;
let paths = new Set();
let suffixes = new Map(); // trailing path segments -> full path, or null when ambiguous

export async function loadPaths(repoId) {
  currentRepo = repoId;
  paths = new Set();
  suffixes = new Map();
  if (!repoId) return;
  try {
    const files = await api.files(repoId);
    if (currentRepo !== repoId) return; // the user switched repository while this was loading
    paths = new Set(files.map((f) => f.path));
    for (const full of paths) {
      const parts = full.split("/");
      for (let i = 1; i < parts.length; i++) {
        const tail = parts.slice(i).join("/");
        suffixes.set(tail, suffixes.has(tail) ? null : full);
      }
    }
  } catch {
    // Not indexed yet (or server error): path links simply stay disabled.
  }
}

export function resolveIndexedPath(candidate) {
  if (!candidate) return null;
  if (paths.has(candidate)) return candidate;
  const trimmed = candidate.replace(/^\.?\//, "");
  if (paths.has(trimmed)) return trimmed;
  return suffixes.get(trimmed) || null;
}

export const indexedPaths = () => paths;
