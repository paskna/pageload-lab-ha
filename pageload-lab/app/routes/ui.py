"""Server-rendered Ingress-compatible administration pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def context(request: Request, active: str, **values: object) -> dict[str, object]:
    # Supervisor Ingress normally supplies ``root_path``.  Some HA versions
    # omit the header on the first document request; a relative base keeps
    # onboarding assets inside the current Ingress mount instead of falling
    # back to the host root (which makes the page appear completely unstyled).
    root = request.scope.get("root_path", "").rstrip("/") or "."
    return {
        "request": request,
        "active": active,
        "base_path": root,
        "version": request.app.state.version,
        "csrf_token": request.app.state.csrf_token,
        "csp_nonce": getattr(request.state, "csp_nonce", ""),
        **values,
    }


@router.get("/", response_class=HTMLResponse, name="dashboard")
async def dashboard(request: Request) -> HTMLResponse:
    if not request.app.state.settings.get("onboarding_complete", False):
        return templates.TemplateResponse(request, "onboarding.html", context(request, "onboarding"))
    return templates.TemplateResponse(request, "dashboard.html", context(request, "dashboard"))


@router.get("/tests", response_class=HTMLResponse, name="tests")
async def tests_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "tests.html", context(request, "tests"))


@router.get("/tests/new", response_class=HTMLResponse, name="new_test")
async def new_test_page(request: Request) -> HTMLResponse:
    proxies = request.app.state.proxies.list_public()
    settings = request.app.state.settings.all()
    return templates.TemplateResponse(request, "test_form.html", context(request, "new", proxies=proxies, settings=settings))


@router.get("/tests/{test_id}", response_class=HTMLResponse, name="test_detail")
async def test_detail_page(request: Request, test_id: int) -> HTMLResponse:
    try:
        test = request.app.state.test_service.get(test_id)
    except KeyError:
        return templates.TemplateResponse(request, "error.html", context(request, "tests", message="Der Test wurde nicht gefunden."), status_code=404)
    return templates.TemplateResponse(request, "test_detail.html", context(request, "tests", test=test))


@router.get("/reports", response_class=HTMLResponse, name="reports")
async def reports_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "reports.html", context(request, "reports"))


@router.get("/proxies", response_class=HTMLResponse, name="proxies")
async def proxies_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "proxies.html", context(request, "proxies"))


@router.get("/settings", response_class=HTMLResponse, name="settings")
async def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "settings.html", context(request, "settings", settings=request.app.state.settings.all()))


@router.get("/system", response_class=HTMLResponse, name="system")
async def system_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "system.html", context(request, "system"))
