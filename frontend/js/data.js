// Server-backed refreshers shared by every view, plus polling of background index runs.
import { api } from "./api.js";
import { emit, setFeatures, setRepos, state } from "./state.js";
import { loadPaths } from "./paths.js";

export async function refreshRepos() {
  const repos = await api.repos();
  for (const repo of repos) {
    if (repo.index) state.indexStatus[repo.id] = repo.index;
  }
  setRepos(repos);
  for (const repo of repos) {
    if (repo.index && repo.index.state === "running") trackIndex(repo.id);
  }
  return repos;
}

export async function refreshFeatures() {
  const id = state.repoId;
  if (!id) {
    setFeatures([]);
    await loadPaths(null);
    return;
  }
  try {
    const features = await api.features(id);
    if (state.repoId === id) setFeatures(features);
  } catch {
    if (state.repoId === id) setFeatures([]);
  }
  await loadPaths(id);
}

const timers = new Map();

export function trackIndex(repoId) {
  if (timers.has(repoId)) return;
  const stop = () => {
    clearInterval(timers.get(repoId));
    timers.delete(repoId);
  };
  const tick = async () => {
    let status;
    try {
      status = await api.indexStatus(repoId);
    } catch {
      stop();
      return;
    }
    state.indexStatus[repoId] = status;
    emit("index", { repoId, status });
    if (status.state !== "running") {
      stop();
      try {
        await refreshRepos();
        if (repoId === state.repoId) await refreshFeatures();
      } catch { /* the next manual refresh will surface the problem */ }
    }
  };
  timers.set(repoId, setInterval(tick, 1000));
  tick();
}
