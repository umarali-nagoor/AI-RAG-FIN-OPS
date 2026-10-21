"""FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.security import RateLimiter, SecurityMiddleware, make_auth_dependency
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.llm.providers import ProviderError
from app.models import (
    AnalyzeResponse,
    AskRequest,
    AskResponse,
    OptimizeResponse,
    PRSummaryOut,
    RepoRequest,
    RepositoryInfo,
    ReviewPRRequest,
    ReviewPRResponse,
)
from app.services import analytics
from app.services.copilot import Copilot
from app.services.github import make_github_client
from app.services.platform import Platform

log = logging.getLogger("finops.api")


def _validate_production(settings: Settings) -> None:
    if settings.is_production and not settings.api_key_list:
        raise RuntimeError("FINOPS_API_KEYS must be set when FINOPS_ENVIRONMENT=production")
    if settings.is_production and any(len(k) < 24 for k in settings.api_key_list):
        raise RuntimeError("API keys must be at least 24 characters")


def create_app(settings: Settings | None = None, platform: Platform | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    _validate_production(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from app.core.pricing import warm_price_cache
        warm_price_cache()
        app.state.platform = platform or Platform(settings)
        app.state.copilot = Copilot(app.state.platform)
        app.state.github = make_github_client(settings)
        app.state.platform.bootstrap()
        if settings.index_on_start:
            app.state.platform.all_states()
        yield

    app = FastAPI(
        title="IBM Cloud Logs FinOps Copilot",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.add_middleware(SecurityMiddleware, settings=settings, limiter=RateLimiter(settings.rate_limit_per_minute))
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
            max_age=600,
        )

    # -- generic error handling: details go to the server log only -----------------------------
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": "Invalid request"}, status_code=422)

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"error": exc.detail if exc.status_code < 500 else "Internal error"},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", exc_info=exc)
        return JSONResponse({"error": "Internal error"}, status_code=500)

    auth = make_auth_dependency(settings)
    api = APIRouter(dependencies=[Depends(auth)])

    def copilot(request: Request) -> Copilot:
        return request.app.state.copilot

    def platform_of(request: Request) -> Platform:
        return request.app.state.platform

    def guard(fn, *args):  # noqa: ANN001, ANN202 - maps domain errors to safe HTTP errors
        try:
            return fn(*args)
        except KeyError:
            raise HTTPException(404, "Repository not found") from None
        except FileNotFoundError:
            raise HTTPException(409, "Repository source is not available on this server") from None
        except ProviderError:
            raise HTTPException(502, "Upstream model provider error") from None

    @api.get("/repositories", response_model=list[RepositoryInfo])
    def list_repositories(request: Request):
        return platform_of(request).repositories()

    @api.post("/analyze-repository", response_model=AnalyzeResponse)
    def analyze_repository(body: RepoRequest, request: Request):
        return guard(copilot(request).analyze, body.repository)

    @api.post("/optimize-repository", response_model=OptimizeResponse)
    def optimize_repository(body: RepoRequest, request: Request):
        return guard(copilot(request).optimize, body.repository)

    @api.post("/review-pr", response_model=ReviewPRResponse)
    def review_pr(body: ReviewPRRequest, request: Request):
        if len(body.diff.encode()) > settings.max_diff_bytes:
            raise HTTPException(413, "Diff too large")
        return guard(copilot(request).review_pr, body.repository, body.diff)

    @api.post("/ask", response_model=AskResponse)
    def ask(body: AskRequest, request: Request):
        if len(body.question) > settings.max_question_chars:
            raise HTTPException(422, "Question too long")
        if body.diff and len(body.diff.encode()) > settings.max_diff_bytes:
            raise HTTPException(413, "Diff too large")
        return guard(copilot(request).ask, body.question, body.repository, body.diff)

    @api.get("/pull-requests/{repository}", response_model=list[PRSummaryOut])
    def list_pull_requests(repository: str, request: Request, state: str = "open"):
        gh = request.app.state.github
        if gh is None:
            raise HTTPException(501, "GitHub integration not configured — set FINOPS_GITHUB_TOKEN and FINOPS_GITHUB_ORG")
        # Validate repository is in the allow-list
        pf = platform_of(request)
        if repository not in pf.registry.specs:
            raise HTTPException(404, "Repository not found")
        try:
            prs = gh.list_prs(repository, state=state)
        except KeyError:
            raise HTTPException(404, "Repository not found on GitHub") from None
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        return [
            PRSummaryOut(
                number=pr.number,
                title=pr.title,
                author=pr.author,
                branch=pr.branch,
                base=pr.base,
                state=pr.state,
                url=pr.url,
                createdAt=pr.created_at,
                updatedAt=pr.updated_at,
            )
            for pr in prs
        ]

    @api.get("/pull-requests/{repository}/{pr_number}/diff")
    def get_pr_diff(repository: str, pr_number: int, request: Request):
        gh = request.app.state.github
        if gh is None:
            raise HTTPException(501, "GitHub integration not configured — set FINOPS_GITHUB_TOKEN and FINOPS_GITHUB_ORG")
        pf = platform_of(request)
        if repository not in pf.registry.specs:
            raise HTTPException(404, "Repository not found")
        try:
            diff = gh.get_diff(repository, pr_number, settings.max_diff_bytes)
        except KeyError:
            raise HTTPException(404, "PR not found") from None
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
        except ValueError as e:
            raise HTTPException(413, str(e)) from None
        return {"repository": repository, "pr_number": pr_number, "diff": diff}

    @api.get("/pricing")
    def get_pricing():
        from app.core.pricing import current_prices
        return current_prices()

    @api.get("/analytics/repositories")
    def platform_table(request: Request):
        return {"repositories": analytics.table(platform_of(request).all_states())}

    @api.post("/admin/reindex")
    def reindex(request: Request, body: dict | None = None):
        name = (body or {}).get("repository")
        if name is not None and (not isinstance(name, str) or name not in platform_of(request).registry.specs):
            raise HTTPException(404, "Repository not found")
        return {"reindexed": platform_of(request).reindex(name)}

    @api.post("/admin/reload-cost-reports")
    def reload_reports(request: Request):
        platform_of(request).reload_reports()
        return {"status": "ok"}

    app.include_router(api)  # /analyze-repository, /optimize-repository, /review-pr (spec paths)
    app.include_router(api, prefix="/api")  # same routes under /api for the web UI

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz(request: Request):
        ready = getattr(request.app.state, "platform", None) is not None and request.app.state.platform.ready
        return JSONResponse({"status": "ready" if ready else "starting"}, status_code=200 if ready else 503)

    static = settings.static_dir
    if static.is_dir() and (static / "index.html").is_file():
        if (static / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(Path(static / "index.html"))

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn app.main:app_factory --factory``."""
    return create_app()
