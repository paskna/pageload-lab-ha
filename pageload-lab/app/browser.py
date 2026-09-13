"""Real Chromium and lightweight HTTP visit execution."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse

import httpx
import psutil
from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Playwright, TimeoutError as PlaywrightTimeoutError, async_playwright

from .proxy_manager import ProxyConfig
from .security import validate_target_url

LOGGER = logging.getLogger(__name__)
CapacityCallback = Callable[[bool], Awaitable[None]]


@dataclass
class VisitResult:
    started_at: datetime
    finished_at: datetime
    http_status: int | None
    success: bool
    navigation_time_ms: float | None = None
    load_time_ms: float | None = None
    ttfb_ms: float | None = None
    dom_content_loaded_ms: float | None = None
    load_event_ms: float | None = None
    stay_time_seconds: float = 0.0
    error_type: str | None = None
    error_message: str | None = None


class BrowserEngine:
    def __init__(self, settings: object) -> None:
        self.settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._launch_lock = asyncio.Lock()
        self._global_semaphore: asyncio.Semaphore | None = None
        self.active_sessions = 0
        self.waiting_sessions = 0
        self.last_error: str | None = None
        self.started = False

    def _setting(self, name: str, default: object) -> object:
        return self.settings.get(name, default)  # type: ignore[attr-defined]

    async def start(self) -> None:
        if self.started:
            return
        self._global_semaphore = asyncio.Semaphore(int(self._setting("max_parallel_browsers", 5)))
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-extensions",
                    "--no-first-run",
                    f"--renderer-process-limit={int(self._setting('max_parallel_browsers', 5)) + 2}",
                ],
            )
            self.started = True
            self.last_error = None
            LOGGER.info("browser_engine_started")
        except Exception as exc:
            self.last_error = type(exc).__name__
            LOGGER.exception("browser_engine_start_failed")
            await self._close_components()
            raise RuntimeError("Chromium konnte nicht gestartet werden.") from exc

    async def _ensure_browser(self) -> Browser:
        zombies = self._zombie_processes()
        if zombies:
            self.last_error = "zombie_process"
            LOGGER.warning("browser_zombie_process_detected", extra={"count": len(zombies)})
            await self._close_components()
            self.started = False
        if self._browser and self._browser.is_connected():
            return self._browser
        async with self._launch_lock:
            if self._browser and self._browser.is_connected():
                return self._browser
            await self._close_components()
            self.started = False
            await self.start()
            assert self._browser is not None
            return self._browser

    async def health(self) -> dict[str, object]:
        zombie_count = len(self._zombie_processes())
        return {
            "available": self.started and bool(self._browser and self._browser.is_connected()),
            "active_sessions": self.active_sessions,
            "waiting_sessions": self.waiting_sessions,
            "zombie_processes": zombie_count,
            "last_error": self.last_error,
        }

    async def visit(
        self,
        url: str,
        mode: str,
        stay_seconds: float,
        proxy: ProxyConfig | None,
        capacity_callback: CapacityCallback | None = None,
    ) -> VisitResult:
        await validate_target_url(url, bool(self._setting("allow_private_targets", False)))
        await self._wait_for_memory(capacity_callback)
        semaphore = self._global_semaphore
        if semaphore is None:
            await self.start()
            semaphore = self._global_semaphore
        assert semaphore is not None
        queued = semaphore.locked()
        waiting_registered = False
        if queued:
            self.waiting_sessions += 1
            waiting_registered = True
            if capacity_callback:
                await capacity_callback(True)
        try:
            async with semaphore:
                if waiting_registered:
                    self.waiting_sessions = max(0, self.waiting_sessions - 1)
                    waiting_registered = False
                    if capacity_callback:
                        await capacity_callback(False)
                self.active_sessions += 1
                try:
                    if mode == "http":
                        return await self._http_visit(url, proxy)
                    return await self._browser_visit(url, stay_seconds, proxy)
                finally:
                    self.active_sessions = max(0, self.active_sessions - 1)
        finally:
            if waiting_registered:
                self.waiting_sessions = max(0, self.waiting_sessions - 1)

    async def _wait_for_memory(self, capacity_callback: CapacityCallback | None = None) -> None:
        limit = int(self._setting("max_memory_percent", 85))
        waited = 0
        waiting = False
        try:
            while psutil.virtual_memory().percent >= limit:
                if waited == 0:
                    waiting = True
                    if capacity_callback:
                        await capacity_callback(True)
                    LOGGER.warning("browser_capacity_memory_wait", extra={"memory_percent": psutil.virtual_memory().percent, "limit": limit})
                await asyncio.sleep(1)
                waited += 1
                if waited >= 60:
                    raise RuntimeError("Browser-Kapazität bleibt wegen hoher Speicherauslastung gesperrt.")
        finally:
            if waiting and capacity_callback:
                await capacity_callback(False)

    @staticmethod
    def _zombie_processes() -> list[psutil.Process]:
        zombies: list[psutil.Process] = []
        try:
            for child in psutil.Process().children(recursive=True):
                try:
                    if child.status() == psutil.STATUS_ZOMBIE:
                        zombies.append(child)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
            # Home Assistant's AppArmor profile intentionally hides the
            # global /proc process listing.  Zombie detection is a safety
            # enhancement, not a health prerequisite; treat an inaccessible
            # process table as "no observable zombies" and keep the API up.
            pass
        return zombies

    async def _browser_visit(self, url: str, stay_seconds: float, proxy: ProxyConfig | None) -> VisitResult:
        started_at = datetime.now(timezone.utc)
        started_clock = time.perf_counter()
        context: BrowserContext | None = None
        status: int | None = None
        try:
            browser = await self._ensure_browser()
            kwargs = {"ignore_https_errors": False, "user_agent": "PageLoadLab/0.1 (+authorized-test)"}
            if proxy:
                kwargs["proxy"] = proxy.playwright()
            context = await browser.new_context(**kwargs)  # type: ignore[arg-type]
            validated_origins: set[tuple[str, str, int | None]] = set()

            async def protect_request(route: object, request: object) -> None:
                try:
                    request_url = request.url  # type: ignore[attr-defined]
                    parsed = urlparse(request_url)
                    if parsed.scheme not in {"http", "https"}:
                        await route.continue_()  # type: ignore[attr-defined]
                        return
                    origin = (parsed.scheme, parsed.hostname or "", parsed.port)
                    if origin not in validated_origins:
                        await validate_target_url(request_url, bool(self._setting("allow_private_targets", False)))
                        validated_origins.add(origin)
                    await route.continue_()  # type: ignore[attr-defined]
                except PlaywrightError:
                    # The context may have been closed while a final resource
                    # callback was in flight. It is already being reclaimed.
                    return
                except Exception:
                    try:
                        await route.abort("blockedbyclient")  # type: ignore[attr-defined]
                    except PlaywrightError:
                        return

            await context.route("**/*", protect_request)
            page = await context.new_page()
            timeout_ms = int(self._setting("navigation_timeout_seconds", 30)) * 1000
            page.set_default_navigation_timeout(timeout_ms)
            response = await page.goto(url, wait_until="load")
            status = response.status if response else None
            timing = await page.evaluate(
                """() => {
                  const n = performance.getEntriesByType('navigation')[0];
                  if (!n) return null;
                  return {duration:n.duration, requestStart:n.requestStart,
                    responseStart:n.responseStart,
                    domContentLoadedEventEnd:n.domContentLoadedEventEnd,
                    loadEventEnd:n.loadEventEnd};
                }"""
            )
            held_started = time.perf_counter()
            if stay_seconds > 0:
                await asyncio.sleep(stay_seconds)
            actual_stay = time.perf_counter() - held_started if stay_seconds > 0 else 0.0
            total_ms = (time.perf_counter() - started_clock) * 1000
            timing = timing or {}
            success = status is not None and 200 <= status < 400
            return VisitResult(
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                http_status=status,
                success=success,
                navigation_time_ms=round(float(timing.get("duration") or total_ms), 2),
                load_time_ms=round(float(timing.get("duration") or total_ms), 2),
                ttfb_ms=round(float(timing.get("responseStart", 0)) - float(timing.get("requestStart", 0)), 2) if timing else None,
                dom_content_loaded_ms=round(float(timing.get("domContentLoadedEventEnd", 0)), 2) if timing else None,
                load_event_ms=round(float(timing.get("loadEventEnd", 0)), 2) if timing else None,
                stay_time_seconds=round(actual_stay, 3),
                error_type=None if success else "http_status",
                error_message=None if success else f"HTTP-Status {status or 'unbekannt'}",
            )
        except Exception as exc:
            self.last_error = type(exc).__name__
            kind, message = self._classify_error(exc, proxy)
            LOGGER.warning("browser_visit_failed", extra={"error_type": kind, "target_host": httpx.URL(url).host})
            return VisitResult(
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                http_status=status,
                success=False,
                navigation_time_ms=round((time.perf_counter() - started_clock) * 1000, 2),
                load_time_ms=round((time.perf_counter() - started_clock) * 1000, 2),
                error_type=kind,
                error_message=message,
            )
        finally:
            if context:
                try:
                    # Wait for SSRF route callbacks before stopping Playwright. This
                    # avoids orphaned futures during add-on shutdown.
                    await context.unroute_all(behavior="wait")
                    await context.close()
                except PlaywrightError:
                    LOGGER.warning("browser_context_close_failed")

    async def _http_visit(self, url: str, proxy: ProxyConfig | None) -> VisitResult:
        started_at = datetime.now(timezone.utc)
        started_clock = time.perf_counter()
        try:
            timeout = int(self._setting("navigation_timeout_seconds", 30))
            async with httpx.AsyncClient(
                proxy=proxy.url if proxy else None,
                timeout=timeout,
                follow_redirects=False,
                headers={"User-Agent": "PageLoadLab/0.1 (+authorized-test)"},
            ) as client:
                current_url = url
                for _ in range(11):
                    await validate_target_url(current_url, bool(self._setting("allow_private_targets", False)))
                    response = await client.get(current_url)
                    if response.status_code not in {301, 302, 303, 307, 308} or "location" not in response.headers:
                        break
                    current_url = urljoin(current_url, response.headers["location"])
                else:
                    raise httpx.TooManyRedirects("Mehr als 10 Weiterleitungen")
            elapsed = (time.perf_counter() - started_clock) * 1000
            success = 200 <= response.status_code < 400
            return VisitResult(
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                http_status=response.status_code,
                success=success,
                navigation_time_ms=round(elapsed, 2),
                load_time_ms=round(elapsed, 2),
                stay_time_seconds=0,
                error_type=None if success else "http_status",
                error_message=None if success else f"HTTP-Status {response.status_code}",
            )
        except Exception as exc:
            kind, message = self._classify_error(exc, proxy)
            return VisitResult(
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                http_status=None,
                success=False,
                navigation_time_ms=round((time.perf_counter() - started_clock) * 1000, 2),
                load_time_ms=round((time.perf_counter() - started_clock) * 1000, 2),
                error_type=kind,
                error_message=message,
            )

    @staticmethod
    def _classify_error(exc: Exception, proxy: ProxyConfig | None) -> tuple[str, str]:
        text = str(exc).lower()
        if isinstance(exc, (PlaywrightTimeoutError, httpx.TimeoutException)) or "timeout" in text:
            return "timeout", "Die Ziel-URL hat das Zeitlimit überschritten."
        if proxy and ("proxy" in text or isinstance(exc, (httpx.ProxyError, httpx.ConnectError))):
            return "proxy", "Der konfigurierte Proxy ist nicht erreichbar."
        if isinstance(exc, httpx.ConnectError) and ("name or service" in text or "nodename" in text):
            return "dns", "Der Hostname der Ziel-URL konnte nicht aufgelöst werden."
        if "name_not_resolved" in text or "dns" in text:
            return "dns", "Der Hostname der Ziel-URL konnte nicht aufgelöst werden."
        if "browser has been closed" in text or "target page, context or browser has been closed" in text:
            return "browser_crash", "Chromium wurde unerwartet beendet; die Browser Engine wird neu gestartet."
        return "browser" if isinstance(exc, PlaywrightError) else "connection", "Die Ziel-URL konnte nicht erreicht werden."

    async def version(self) -> str | None:
        try:
            browser = await self._ensure_browser()
            return browser.version
        except Exception:
            return None

    async def stop(self) -> None:
        await self._close_components()
        self.started = False
        LOGGER.info("browser_engine_stopped")

    async def _close_components(self) -> None:
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
