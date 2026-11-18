"""FastAPI application factory: JSON API under /api, static single-page UI at /."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import learn, repos, system
from app.config import Settings
from app.llm.base import LLMError
from app.runtime import Runtime

log = logging.getLogger("senior_tutor")
LOOPBACK = {"localhost", "127.0.0.1", "::1", "testserver"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


def _host_only(value: str) -> str:
    return (urlparse("//" + value).hostname or "").lower()


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = Runtime(settings)
    app = FastAPI(title="Senior Tutor", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.runtime = runtime

    configured = {h.strip().lower() for h in runtime.settings.allowed_hosts.split(",") if h.strip()}
    allow_all_hosts = "*" in configured
    allowed_hosts = LOOPBACK | configured

    @app.middleware("http")
    async def security(request: Request, call_next):
        # Protects against DNS rebinding and cross-site requests to a local service that can read local files.
        if not allow_all_hosts and _host_only(request.headers.get("host", "")) not in allowed_hosts:
            return JSONResponse({"detail": "Host not allowed. Set ALLOWED_HOSTS to permit this hostname."}, status_code=400)
        origin = request.headers.get("origin")
        if origin and request.method not in SAFE_METHODS:
            if urlparse(origin).netloc.lower() != request.headers.get("host", "").lower():
                return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", CSP)
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/docs"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(LookupError)
    async def not_found(_: Request, exc: LookupError):
        return JSONResponse({"detail": str(exc.args[0]) if exc.args else "Not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def bad_request(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(PermissionError)
    async def forbidden(_: Request, exc: PermissionError):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(LLMError)
    async def llm_error(_: Request, exc: LLMError):
        return JSONResponse({"detail": f"LLM error: {exc}"}, status_code=502)

    for module in (repos, learn, system):
        app.include_router(module.router, prefix="/api")

    frontend = Path(os.environ.get("FRONTEND_DIR") or Path(__file__).resolve().parents[2] / "frontend")
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="ui")
    else:
        log.warning("Frontend directory not found at %s; serving the API only", frontend)
    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
