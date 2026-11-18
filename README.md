# Senior Tutor — Personal AI Codebase Tutor

![Senior Tutor banner](assets/senior-tutor-banner.png)

A local-first mentor that learns **your** source repositories and teaches them back the way a senior engineer would:
architecture overviews, feature deep dives, request/data-flow traces, concept comparisons, session preparation,
quizzes and mock interviews, and troubleshooting from pasted logs or stack traces.

Every claim is grounded in the code: answers cite numbered sources (`[S1]`, `[S2]` …) that open the exact file and
lines in a side drawer. Citations that were not in the retrieved context are flagged as unverified. If no language
model is available the app still works in an **evidence-only** mode that shows what the index found.

## Quick start (Docker)

```bash
docker run -d --name senior-tutor -p 127.0.0.1:8000:8000 \
  -e ALLOWED_ROOTS=/repos \
  -v tutor-data:/data \
  -v "/path/to/your/repos:/repos:ro" \
  sabya610/senior-tutor:latest
```

Open <http://localhost:8000>, go to **My Repositories**, add a path under `/repos` (for example `/repos/my-service`),
and click **Index**.

Port 8000 already taken? Change the first number: `-p 127.0.0.1:8080:8000` (with Compose: `TUTOR_PORT=8080 docker compose up -d`)
and open <http://localhost:8080> instead.

With Docker Compose (builds locally, or pulls the published image):

```bash
mkdir repos            # put or symlink repositories here
docker compose up -d
```

### Choosing a language model

| Goal | Configuration |
|------|---------------|
| OpenAI / OpenAI-compatible | `-e OPENAI_API_KEY=...` (optionally `OPENAI_BASE_URL`, `LLM_MODEL`) |
| Ollama on the Docker host | `-e OLLAMA_BASE_URL=http://host.docker.internal:11434` (add `--add-host=host.docker.internal:host-gateway` on Linux) |
| No model | nothing — evidence-only mode |

You can also pick the provider and model on the **Settings** page. API keys are read from environment variables only;
they are never entered into, or stored by, the browser.

> Local models on small machines can be slow. If a model call exceeds `LLM_TIMEOUT_SECONDS` (default 180) the tutor
> falls back to an evidence-only answer and says so.

## Run from source

Requirements: Python 3.11+.

```bash
cd backend
python -m venv ../.venv && ../.venv/bin/pip install -r requirements-dev.txt   # Windows: ..\.venv\Scripts\pip
../.venv/bin/python -m uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8000
../.venv/bin/python -m pytest -q                                              # run the tests
```

The UI is plain HTML, CSS and ES modules (no build step) served by the same process from `frontend/`.

## Configuration

All settings are environment variables (a `.env` file in `backend/` also works).

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATA_DIR` | `data` (`/data` in Docker) | SQLite database and settings |
| `ALLOWED_ROOTS` | empty | Comma-separated roots; repositories can only be registered beneath them. **Set this in any shared or containerised setup.** |
| `ALLOWED_HOSTS` | empty | Extra hostnames accepted in the `Host` header. Loopback is always allowed; `*` disables the check (only behind a trusted proxy). |
| `LLM_PROVIDER` | `auto` | `auto`, `openai`, `ollama`, `mock` |
| `LLM_MODEL` | provider default | Model name |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | empty | OpenAI-compatible endpoint |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `LLM_TIMEOUT_SECONDS` | `180` | Per-call timeout before falling back to evidence-only |
| `EMBED_PROVIDER` / `EMBED_MODEL` | `auto` | `auto`, `openai`, `ollama`, `local` (offline hashing) |
| `FRONTEND_DIR` | `<repo>/frontend` | Static UI directory |
| `LOG_LEVEL` | `INFO` | Python log level |

## Security notes

The service can read source code on the machine it runs on, so it is hardened accordingly:

- binds to loopback by default in Compose; set `ALLOWED_ROOTS` to restrict which paths can be indexed
- `Host` allow-list (DNS-rebinding defence) and same-origin enforcement on every state-changing request
- strict Content-Security-Policy (no inline scripts), `nosniff`, `no-referrer`
- the container runs as a non-root user and the repository mount is read-only
- secrets come from the environment only and are never returned by the API

## Project layout

```
backend/app   FastAPI service (scanner, retrieval, teaching, knowledge store, LLM providers)
frontend/     Vanilla-JS single-page app (hash router, no build step; Mermaid is vendored)
docs/         Architecture notes
Dockerfile, docker-compose.yml
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the pieces fit together and how to extend them.
