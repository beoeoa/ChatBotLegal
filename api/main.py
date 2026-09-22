# Load environment variables
from dotenv import load_dotenv

load_dotenv()

import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.auth import PasswordAuthMiddleware, production_mode_enabled
from api import gateway_rate_limit
from api.auth_rate_limit import AuthRateLimitMiddleware
from api import readiness
from api.legal_crawl_service import LegalCrawlService, legal_crawl_scheduler_loop, legal_import_worker_loop
from api.observability import RuntimeTelemetryMiddleware
from api.retention_service import retention_scheduler_loop
from api.legal_effectivity_service import legal_effectivity_scheduler_loop
from api.routers import (
    auth,
    chat,
    config,
    context,
    credentials,
    embedding,
    embedding_rebuild,
    insights,
    languages,
    legal_search,
    legal_management,
    models,
    notebooks,
    notes,
    search,
    settings,
    source_chat,
    sources,
    users,
    ward_procedures,
    legal_crawl_api,
    faq,
    ask_sessions,
    live_support,
    conversations,
    admin_control,
    admin_activity,
    admin_capabilities,
    admin_backups,
    procedure_forms_catalog,
    chat_memory,
    public_quick_chat,

)
from api.routers import commands as commands_router
from open_notebook.database.async_migrate import AsyncMigrationManager
from open_notebook.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ExternalServiceError,
    InvalidInputError,
    NetworkError,
    NotFoundError,
    OpenNotebookError,
    RateLimitError,
)
from open_notebook.utils.encryption import get_secret_from_env


def _parse_cors_origins(raw: str) -> list[str]:
    """Parse CORS_ORIGINS env value into a list of origins."""
    value = raw.strip()
    if value == "*":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


# Parsed once at module load; CORS_ORIGINS changes require a restart.
_cors_origins_raw = os.getenv("CORS_ORIGINS")
CORS_ALLOWED_ORIGINS = _parse_cors_origins(_cors_origins_raw or "*")
CORS_IS_DEFAULT_WILDCARD = _cors_origins_raw is None
PRODUCTION_MODE = production_mode_enabled()
if PRODUCTION_MODE and (
    CORS_IS_DEFAULT_WILDCARD or "*" in CORS_ALLOWED_ORIGINS
):
    raise RuntimeError("CORS_ORIGINS must be explicit in production mode")


def _cors_headers(request: Request) -> dict[str, str]:
    """
    Build CORS headers for error responses.

    Mirrors Starlette CORSMiddleware behavior: reflects the request Origin
    when the origin is allowed (or when wildcard is configured, since
    browsers reject `Access-Control-Allow-Origin: *` combined with
    credentials). Omits `Access-Control-Allow-Origin` for disallowed
    origins so the browser blocks the error body from leaking cross-origin.
    """
    origin = request.headers.get("origin")
    headers: dict[str, str] = {
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    if origin and ("*" in CORS_ALLOWED_ORIGINS or origin in CORS_ALLOWED_ORIGINS):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"

    return headers


def _prewarm_simplified_generation_runtime() -> None:
    """Import the lazy model adapter before the first simplified request."""

    # Esperanto loads optional transformer/provider adapters on first import.
    # Doing that inside the first user's 18-second generation window leaves too
    # little time for DeepSeek. Startup is the correct place to pay this one-off
    # cost when the simplified serving path is explicitly enabled.
    from esperanto import LanguageModel  # noqa: F401


# Import commands to register them in the API process
try:
    logger.info("Commands imported in API process")
except Exception as e:
    logger.error(f"Failed to import commands in API process: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan event handler for the FastAPI application.
    Runs database migrations automatically on startup.
    """
    # Startup: Security checks
    logger.info("Starting API initialization...")

    # Security check: Encryption key
    if not get_secret_from_env("OPEN_NOTEBOOK_ENCRYPTION_KEY"):
        logger.warning(
            "OPEN_NOTEBOOK_ENCRYPTION_KEY not set. "
            "API key encryption will fail until this is configured. "
            "Set OPEN_NOTEBOOK_ENCRYPTION_KEY to any secret string."
        )

    # Run database migrations

    try:
        migration_manager = AsyncMigrationManager()
        current_version = await migration_manager.get_current_version()
        logger.info(f"Current database version: {current_version}")

        # We already fetched the current version above. Avoid a second remote
        # version query on every process start (cold Surreal connections are
        # noticeably expensive on local deployments).
        if current_version < len(migration_manager.up_migrations):
            logger.warning("Database migrations are pending. Running migrations...")
            await migration_manager.run_migration_up()
            new_version = await migration_manager.get_current_version()
            logger.success(
                f"Migrations completed successfully. Database is now at version {new_version}"
            )
        else:
            logger.info(
                "Database is already at the latest version. No migrations needed."
            )
    except Exception as e:
        logger.error(f"CRITICAL: Database migration failed: {str(e)}")
        logger.exception(e)
        # Fail fast - don't start the API with an outdated database schema
        raise RuntimeError(f"Failed to run database migrations: {str(e)}") from e

    # Direct RAG is the only public answer runtime in this release. Provision
    # its shared adapters at startup; rollback is an application image, not a
    # feature flag that revives a second in-process pipeline.
    async def _background_warmups() -> None:
        """Warm optional providers without holding the API socket closed."""
        from api.routers.search import prewarm_search_runtime, prewarm_simplified_answer_model

        async def warm_chat() -> None:
            try:
                await prewarm_simplified_answer_model()
                logger.info("Chat gateway default connection prewarmed")
            except Exception as exc:
                logger.warning("Chat gateway prewarm skipped: {}", type(exc).__name__)

        async def warm_search() -> None:
            try:
                warmup = await prewarm_search_runtime()
                logger.info("Search runtime prewarmed retrieval={} ollama={} completed={}",
                            warmup.get("retrieval"), warmup.get("ollama"), warmup.get("completed"))
            except Exception as exc:
                logger.warning("Search runtime warm-up skipped: {}", type(exc).__name__)

        await asyncio.gather(warm_chat(), warm_search())

    # Provider warm-up may wait on a local retrieval service or model server.
    # It is an optimization, never a readiness dependency for a page/API read.
    warmup_task = asyncio.create_task(_background_warmups())

    logger.success("API initialization completed successfully")

    async def _verify_default_sources() -> None:
        try:
            await asyncio.wait_for(LegalCrawlService.ensure_default_sources(), timeout=15.0)
            logger.info("Default legal crawler sources verified")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Default legal crawler source verification deferred: {}", type(exc).__name__)

    # This is idempotent administration and must not delay the first page/API
    # response while SurrealDB is cold.
    source_task = asyncio.create_task(_verify_default_sources())
    # Dashboard projection is deliberately lazy. Some legacy projection code
    # performs CPU-bound work before its first await, so asyncio.wait_for cannot
    # guarantee startup latency. The endpoint's stale-while-refresh cache owns
    # this work; chat/upload readiness must not depend on it.
    logger.info("Admin dashboard projection deferred to lazy cache")
    crawler_task = None
    import_worker_task = None
    retention_task = None
    effectivity_task = None
    if os.getenv("LEGAL_CRAWLER_ENABLED", "true").lower() not in {"false", "0", "no"}:
        crawler_task = asyncio.create_task(legal_crawl_scheduler_loop())
    if os.getenv("LEGAL_IMPORT_WORKER_ENABLED", "true").lower() not in {"false", "0", "no"}:
        import_worker_task = asyncio.create_task(legal_import_worker_loop())
    if os.getenv("RETENTION_SCHEDULER_ENABLED", "true").lower() not in {"false", "0", "no"}:
        retention_task = asyncio.create_task(retention_scheduler_loop())
    effectivity_task = asyncio.create_task(legal_effectivity_scheduler_loop())
    from api.conversation_outbox import recovery_loop
    conversation_recovery_task = asyncio.create_task(recovery_loop())
    from api.legal_activation_recovery import activation_recovery_loop
    activation_recovery_task = asyncio.create_task(activation_recovery_loop())
    from api.organization_projection_recovery import organization_projection_recovery_loop
    organization_recovery_task = asyncio.create_task(organization_projection_recovery_loop())

    # Yield control to the application
    from api.legal_upstream import close_legal_upstream_client, start_legal_upstream_client
    await start_legal_upstream_client()
    yield

    # Shutdown: cleanup if needed
    warmup_task.cancel()
    with suppress(asyncio.CancelledError):
        await warmup_task
    source_task.cancel()
    with suppress(asyncio.CancelledError):
        await source_task
    organization_recovery_task.cancel()
    with suppress(asyncio.CancelledError):
        await organization_recovery_task
    activation_recovery_task.cancel()
    with suppress(asyncio.CancelledError):
        await activation_recovery_task
    conversation_recovery_task.cancel()
    with suppress(asyncio.CancelledError):
        await conversation_recovery_task
    if crawler_task:
        crawler_task.cancel()
        with suppress(asyncio.CancelledError):
            await crawler_task
    if import_worker_task:
        import_worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await import_worker_task
    if retention_task:
        retention_task.cancel()
        with suppress(asyncio.CancelledError):
            await retention_task
    if effectivity_task:
        effectivity_task.cancel()
        with suppress(asyncio.CancelledError):
            await effectivity_task
    from open_notebook.ai.chat_gateway import close_chat_clients
    await close_chat_clients()
    await close_legal_upstream_client()
    logger.info("API shutdown complete")


app = FastAPI(
    title="Hai Phong Ward and Commune Legal Assistant API",
    description="API for legal lookup and administrative procedure assistance in Hai Phong",
    lifespan=lifespan,
    docs_url=None if PRODUCTION_MODE else "/docs",
    redoc_url=None if PRODUCTION_MODE else "/redoc",
    openapi_url=None if PRODUCTION_MODE else "/openapi.json",
)

from starlette.middleware.base import BaseHTTPMiddleware
from urllib.parse import urlparse

class RelativeRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
            location = response.headers["location"]
            if location.startswith("http://") or location.startswith("https://"):
                parsed = urlparse(location)
                relative_location = parsed.path
                if parsed.query:
                    relative_location += f"?{parsed.query}"
                if parsed.fragment:
                    relative_location += f"#{parsed.fragment}"
                response.headers["location"] = relative_location
        return response

app.add_middleware(RelativeRedirectMiddleware)

if CORS_IS_DEFAULT_WILDCARD:
    logger.warning(
        "CORS_ORIGINS is not set — API accepts cross-origin requests from any "
        "origin (default: '*'). For production deployments, set CORS_ORIGINS to "
        "your frontend origin(s), e.g. "
        "CORS_ORIGINS=https://notebook.example.com"
    )
else:
    logger.info(f"CORS allowed origins: {CORS_ALLOWED_ORIGINS}")

# Add password authentication middleware first
# Exclude /api/auth/status and /api/config from authentication
public_paths = [
    "/",
    "/health",
    "/ready",
    "/ready/import",
    "/ready/answer",
    # Next.js proxies relative API requests by retaining the `/api` prefix.
    # Keep the readiness aliases public as well so the admin UI can report
    # import health before a user action attempts to create a job.
    "/api/ready/import",
    "/api/ready/answer",
    "/api/auth/status",
    "/api/auth/login",
    # Signed short-lived tickets authorize first-login MFA enrollment before
    # a session cookie exists. Both handlers validate the ticket and code.
    "/api/auth/totp/setup",
    "/api/auth/totp/confirm",
    "/api/auth/register",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/firebase/session",
    "/api/auth/firebase/link",
    "/api/config",
    "/api/config/public",
    "/api/public/quick-chat",
    "/internal/gateway-rate-limit",
]
if not PRODUCTION_MODE:
    public_paths.extend([
        "/docs",
        "/openapi.json",
        "/redoc",
    ])

app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(
    PasswordAuthMiddleware,
    excluded_paths=public_paths,
)

# Add CORS middleware last (so it processes first)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Legal-Pdf-Origin", "Content-Disposition", "X-Legal-View-Ms", "X-Legal-Download-Ms"],
)
if PRODUCTION_MODE:
    trusted_hosts = [
        item.strip()
        for item in str(os.getenv("TRUSTED_HOSTS") or "").split(",")
        if item.strip()
    ]
    if not trusted_hosts:
        raise RuntimeError("TRUSTED_HOSTS must be explicit in production mode")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
app.add_middleware(RuntimeTelemetryMiddleware)


# Custom exception handler to ensure CORS headers are included in error responses
# This helps when errors occur before the CORS middleware can process them
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Custom exception handler that ensures CORS headers are included in error responses.
    This is particularly important for 413 (Payload Too Large) errors during file uploads.

    Note: If a reverse proxy (nginx, traefik) returns 413 before the request reaches
    FastAPI, this handler won't be called. In that case, configure your reverse proxy
    to add CORS headers to error responses.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={**(exc.headers or {}), **_cors_headers(request)},
    )


@app.exception_handler(NotFoundError)
async def not_found_error_handler(request: Request, exc: NotFoundError):
    return JSONResponse(
        status_code=404,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(InvalidInputError)
async def invalid_input_error_handler(request: Request, exc: InvalidInputError):
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(AuthenticationError)
async def authentication_error_handler(request: Request, exc: AuthenticationError):
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(RateLimitError)
async def rate_limit_error_handler(request: Request, exc: RateLimitError):
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(ConfigurationError)
async def configuration_error_handler(request: Request, exc: ConfigurationError):
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(NetworkError)
async def network_error_handler(request: Request, exc: NetworkError):
    return JSONResponse(
        status_code=502,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(ExternalServiceError)
async def external_service_error_handler(request: Request, exc: ExternalServiceError):
    return JSONResponse(
        status_code=502,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(OpenNotebookError)
async def open_notebook_error_handler(request: Request, exc: OpenNotebookError):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


# Include routers
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(users.router, prefix="/api", tags=["users"])
app.include_router(config.router, prefix="/api", tags=["config"])
app.include_router(notebooks.router, prefix="/api", tags=["notebooks"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(models.router, prefix="/api", tags=["models"])
app.include_router(notes.router, prefix="/api", tags=["notes"])
app.include_router(embedding.router, prefix="/api", tags=["embedding"])
app.include_router(
    embedding_rebuild.router, prefix="/api/embeddings", tags=["embeddings"]
)
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(context.router, prefix="/api", tags=["context"])
app.include_router(sources.router, prefix="/api", tags=["sources"])
app.include_router(insights.router, prefix="/api", tags=["insights"])
app.include_router(commands_router.router, prefix="/api", tags=["commands"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(source_chat.router, prefix="/api", tags=["source-chat"])
app.include_router(credentials.router, prefix="/api", tags=["credentials"])
app.include_router(languages.router, prefix="/api", tags=["languages"])
from api.procedure_admin import router as procedure_admin_router
from api.chat_upload_store import router as chat_upload_router
app.include_router(chat_upload_router, prefix="/api")
app.include_router(procedure_admin_router, prefix="/api")
app.include_router(ward_procedures.router, prefix="/api", tags=["ward-procedures"])
app.include_router(faq.router, prefix="/api", tags=["faq"])
app.include_router(legal_crawl_api.router, prefix="/api")
app.include_router(legal_search.router, prefix="/api")
app.include_router(legal_management.router, prefix="/api")
app.include_router(ask_sessions.router, prefix="/api", tags=["ask-sessions"])
app.include_router(conversations.router, prefix="/api", tags=["conversations"])
app.include_router(chat_memory.router, prefix="/api", tags=["chat-memory"])
app.include_router(live_support.router, prefix="/api", tags=["live-support"])
app.include_router(admin_control.router, prefix="/api", tags=["admin-control"])
app.include_router(admin_activity.router, prefix="/api", tags=["admin-activity"])
app.include_router(admin_capabilities.router, prefix="/api", tags=["admin-capabilities"])
app.include_router(admin_backups.router, prefix="/api")
app.include_router(procedure_forms_catalog.router, prefix="/api")
app.include_router(gateway_rate_limit.router)
app.include_router(public_quick_chat.router, prefix="/api")


@app.get("/")
async def root():
    return {"message": "Hai Phong legal assistant API is running"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/ready")
async def ready():
    report = await readiness.collect_readiness()
    status_code = 200 if report["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=report)


@app.get("/api/ready/answer", include_in_schema=False)
@app.get("/ready/answer")
async def answer_ready():
    """Readiness for Ask traffic, including the configured chat model."""
    report = await readiness.collect_answer_readiness()
    status_code = 200 if report["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=report)


@app.get("/api/ready/import", include_in_schema=False)
@app.get("/ready/import")
async def import_ready():
    """Readiness for the deterministic legal-import and embedding pipeline."""
    report = await readiness.collect_import_readiness()
    status_code = 200 if report["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=report)
