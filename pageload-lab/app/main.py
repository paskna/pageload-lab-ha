"""PageLoad Lab FastAPI application and lifecycle."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from . import __version__
from .browser import BrowserEngine
from .database import Database
from .logging_config import configure_logging
from .models import Test
from .proxy_manager import ProxyManager
from .reporting import ReportingService
from .routes import api, ui
from .scheduler import SchedulerService
from .security import SecretBox, load_or_create_secret
from .services.system import SystemService
from .services.tests import TestService
from .settings import Paths, SettingsStore
from .worker import TestRunnerManager

LOGGER = logging.getLogger(__name__)
APP_DIR = Path(__file__).resolve().parent


def _install_shutdown_exception_filter(app: FastAPI) -> None:
    """Silence only the two known Playwright driver futures after a clean stop."""
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        exc = context.get("exception")
        expected = {
            "Connection closed while reading from the driver",
            "Target page, context or browser has been closed",
        }
        if (
            getattr(app.state, "shutting_down", False)
            and context.get("message") == "Future exception was never retrieved"
            and exc is not None
            and str(exc) in expected
        ):
            LOGGER.debug("playwright_shutdown_future_drained", extra={"error": type(exc).__name__})
            return
        if previous:
            previous(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


def _log_level(paths: Paths) -> str:
    options = paths.data / "options.json"
    if options.exists():
        try:
            return str(json.loads(options.read_text()).get("log_level", "info"))
        except (OSError, json.JSONDecodeError):
            pass
    return os.environ.get("PAGELAB_LOG_LEVEL", "info")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    paths = Paths.from_environment()
    paths.ensure()
    app.state.log_file = configure_logging(paths.logs, _log_level(paths))
    database = Database(paths.database)
    database.initialize()
    with database.session() as session:
        session.execute(
            update(Test)
            .where(Test.status.in_(["running", "paused", "waiting_capacity"]))
            .values(status="interrupted", status_message="Durch einen Neustart unterbrochen", finished_at=datetime.now(timezone.utc))
        )
    settings = SettingsStore(database.session_factory)
    settings.ensure_defaults()
    proxies = ProxyManager(database.session_factory, SecretBox(paths.secret_key))
    browser = BrowserEngine(settings)
    reporting = ReportingService(database.session_factory)
    runners = TestRunnerManager(database.session_factory, browser, proxies, reporting)
    scheduler = SchedulerService(database.session_factory, runners, reporting, settings)
    system = SystemService(database, scheduler, browser, paths, os.environ.get("PAGELAB_VERSION", __version__))

    app.state.paths = paths
    app.state.database = database
    app.state.settings = settings
    app.state.proxies = proxies
    app.state.browser = browser
    app.state.reporting = reporting
    app.state.runners = runners
    app.state.scheduler = scheduler
    app.state.test_service = TestService(database.session_factory, settings)
    app.state.system = system
    app.state.version = os.environ.get("PAGELAB_VERSION", __version__)
    app.state.csrf_token = load_or_create_secret(paths.csrf_secret)
    app.state.testing = os.environ.get("PAGELAB_TESTING") == "1"
    app.state.shutting_down = False
    _install_shutdown_exception_filter(app)

    try:
        await browser.start()
    except RuntimeError:
        LOGGER.error("browser_engine_unavailable_starting_degraded")
    scheduler.start()
    LOGGER.info("application_started", extra={"version": app.state.version})
    try:
        yield
    finally:
        app.state.shutting_down = True
        scheduler.stop()
        await runners.shutdown()
        await browser.stop()
        database.close()
        gc.collect()
        await asyncio.sleep(0)
        LOGGER.info("application_stopped")


app = FastAPI(
    title="PageLoad Lab",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def ingress_and_security(request: Request, call_next: object):
    # The UI is intentionally reachable only through the Supervisor Ingress
    # gateway. Loopback is reserved for the container health check.
    client_host = request.client.host if request.client else ""
    testing = getattr(request.app.state, "testing", False)
    if not testing and client_host not in {"172.30.32.2", "127.0.0.1", "::1"}:
        return JSONResponse({"error": "Zugriff ist nur über Home Assistant Ingress möglich."}, status_code=403)
    ingress_path = request.headers.get("x-ingress-path", "").rstrip("/")
    if ingress_path:
        request.scope["root_path"] = ingress_path
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith("/api/"):
        expected = getattr(request.app.state, "csrf_token", None)
        if not testing and request.headers.get("x-pageload-csrf") != expected:
            return JSONResponse({"error": "Sicherheitsprüfung fehlgeschlagen. Bitte die Seite neu laden."}, status_code=403)
    response = await call_next(request)  # type: ignore[operator]
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'"
    return response


@app.exception_handler(KeyError)
async def key_error(_: Request, __: KeyError) -> JSONResponse:
    return JSONResponse({"error": "Der angeforderte Datensatz wurde nicht gefunden."}, status_code=404)


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(IntegrityError)
async def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
    LOGGER.warning("database_constraint_failed", extra={"error": type(exc).__name__})
    return JSONResponse({"error": "Dieser Name wird bereits verwendet oder die Daten verletzen eine Datenbankregel."}, status_code=409)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    messages = []
    for item in exc.errors():
        message = str(item.get("msg", "Ungültige Eingabe"))
        if message.startswith("Value error, "):
            message = message[13:]
        messages.append(message)
    return JSONResponse({"error": " ".join(messages)}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    LOGGER.exception("unhandled_request_error", exc_info=exc)
    return JSONResponse(
        {"error": "Die Aktion konnte nicht abgeschlossen werden. Details wurden sicher protokolliert."},
        status_code=500,
    )


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    database_ok = scheduler_ok = False
    try:
        database_ok = request.app.state.database.check_integrity() == "ok"
        scheduler_ok = bool(request.app.state.scheduler.status()["running"])
    except Exception:
        pass
    browser = await request.app.state.browser.health()
    healthy = database_ok and scheduler_ok and bool(browser["available"])
    payload = {
        "status": "healthy" if healthy else "degraded",
        "backend": True,
        "database": database_ok,
        "scheduler": scheduler_ok,
        "browser_engine": browser,
    }
    return JSONResponse(payload, status_code=200 if database_ok and scheduler_ok else 503)


app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.include_router(api.router)
app.include_router(ui.router)
